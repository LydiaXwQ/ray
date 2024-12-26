import asyncio
import logging
from contextlib import contextmanager
from typing import Dict, List, Optional, TypeVar

import ray
from ray.train.v2._internal.constants import (
    DEFAULT_REPORT_BARRIER_TIMEOUT_S,
    DEFAULT_REPORT_BARRIER_WARN_INTERVAL_S,
    REPORT_BARRIER_WARN_INTERVAL_S_ENV_VAR,
)
from ray.train.v2._internal.exceptions import BroadcastCollectiveTimeoutError

T = TypeVar("T", bound=Optional[object])
logger = logging.getLogger(__name__)


BROADCAST_PERIODIC_WARNING = """
`ray.train.report` has not been called by all {world_size} workers in the group.
Please ensure that all workers call `ray.train.report` regardless of whether
they participate in checkpointing or not. Here are the ranks that have reported
so far and how long they have been waiting in seconds:
{time_elapsed_dict}
You can set the {warn_interval_env_var} environment variable to change the frequency
of this warning from its current value: {warn_interval_s} seconds.
"""


@ray.remote(num_cpus=0)  # type: ignore
class SynchronizationActor:
    """A Ray actor that synchronizes the workers in a distributed training job.

    This actor forms a synchronization barrier on a group of processes.
    Every time a worker calls the broadcast_from_rank_zero method,
    the counter is incremented. When the counter equals to the world size,
    the actor notifies all the workers to continue.
    """

    def __init__(
        self,
        timeout_s: float = DEFAULT_REPORT_BARRIER_TIMEOUT_S,
        warn_interval_s: float = DEFAULT_REPORT_BARRIER_WARN_INTERVAL_S,
    ):
        self._counter: int = 0
        self._world_size: int = 0
        self._condition = asyncio.Condition()
        self._reduced_data = None
        # The time when workers from different ranks
        # enters the synchronization barrier.
        self._sync_start_times: List[Optional[float]] = []
        # The timeout in seconds for the synchronization barrier.
        self._timeout_s: float = timeout_s
        # The interval in seconds to log a warning when waiting for the barrier.
        self._warn_interval_s: float = warn_interval_s

    def get_counter(self):
        """Returns the current value of the counter."""
        return self._counter

    def get_world_size(self):
        """Returns the current value of the world_size."""
        return self._world_size

    def get_reduced_data(self):
        """Returns the current value of the reduced_data."""
        return self._reduced_data

    def _clear_states(self):
        """Clears the states of the actor. When the last worker has
        called the _clear_states method, the actor clears its states
        """
        self._counter -= 1
        if self._counter == 0:
            self._reduced_data = None
            self._world_size = 0

    def _setup_or_validate_collective_op(self, world_size: int):
        """The setup method for the synchronization actor if it is not setup yet.
        It initializes the world size and the start times for the
        synchronization barrier.
        """
        if self._world_size == 0:
            self._world_size = world_size
            self._sync_start_times = [None] * world_size
        elif world_size != self._world_size:
            raise ValueError(
                f"Expects all callers to provide the same world size. \
                Got {world_size} and expected {self._world_size}."
            )

    @contextmanager
    def _broadcast_collective_context_manager(
        self, world_rank: int, world_size: int, data: T
    ):
        """A context manager that ensures the synchronization barrier is lifted
        after the block of code is executed.
        """
        try:
            self._setup_or_validate_collective_op(world_size)
            if world_rank == 0:
                self._reduced_data = data
            if self._counter < self._world_size:
                self._counter += 1
            yield
        finally:
            self._clear_states()

    def _get_time_elapsed_dict(self) -> Dict[int, Optional[float]]:
        """
        Returns a dictionary of the time elapsed for each worker since
        it synchronization barrier. If it has not entered the barrier,
        the value is None.
        """
        current_time = asyncio.get_event_loop().time()
        return {
            idx: current_time - start_time if start_time else None
            for idx, start_time in enumerate(self._sync_start_times)
        }

    def _get_broadcast_collective_timeout_error(
        self,
    ) -> BroadcastCollectiveTimeoutError:
        """Creates a BroadcastCollectiveTimeoutError with the current time"""
        time_elapsed = self._get_time_elapsed_dict()
        return BroadcastCollectiveTimeoutError(time_elapsed, self._timeout_s)

    async def _wait_with_logging(self, condition, world_rank: int):
        """Waits for the condition to be notified, logging an warning every
        `log_interval` seconds, and raises a timeout error if `timeout` is reached.
        """
        current_time = asyncio.get_event_loop().time()
        self._sync_start_times[world_rank] = current_time
        while True:
            try:
                await asyncio.wait_for(condition.wait(), timeout=self._warn_interval_s)
                return
            # asyncio.wait_for() raises `asyncio.TimeoutError` for asyncio<=3.10
            # and raises `TimeoutError` for asyncio>=3.11
            # https://docs.python.org/3/library/asyncio-task.html#asyncio.wait_for
            # TODO: (hpguo) Make only one worker log the warning message.
            except (asyncio.TimeoutError, TimeoutError):
                reported_time_elapsed_dict = {
                    k: v
                    for k, v in self._get_time_elapsed_dict().items()
                    if v is not None
                }
                logger.warning(
                    BROADCAST_PERIODIC_WARNING.format(
                        time_elapsed_dict=reported_time_elapsed_dict,
                        world_size=self._world_size,
                        warn_interval_env_var=REPORT_BARRIER_WARN_INTERVAL_S_ENV_VAR,
                        warn_interval_s=self._warn_interval_s,
                    )
                )

    async def broadcast_from_rank_zero(
        self, world_rank: int, world_size: int, data: T
    ) -> T:
        """Broadcasts a data from the worker with rank 0 to all other workers.

        This method is a coroutine that blocks until all workers have called this
        method  with the their data. The data from the worker with rank 0 will
        be returned.
        """
        # Ensures that all global states manipulation is done within the async context
        # manager which makes the condition variable awaiting and the counter
        # incrementing an atomic operation.
        async with self._condition:
            with self._broadcast_collective_context_manager(
                world_rank, world_size, data
            ):
                # If the counter is equal to the world size, it means the last worker
                # has called the broadcast_from_rank_zero method. The actor notifies
                # all the workers to continue.
                if self._counter == self._world_size:
                    self._condition.notify_all()
                    return self._reduced_data
                # If the counter is less than the world size, the actor waits for the
                # other workers to call the broadcast_from_rank_zero method.
                try:
                    await asyncio.wait_for(
                        self._wait_with_logging(self._condition, world_rank),
                        timeout=self._timeout_s,
                    )
                    return self._reduced_data
                except (asyncio.TimeoutError, TimeoutError) as e:
                    raise self._get_broadcast_collective_timeout_error() from e

    # TODO: Implement a general consensus_from_votes method that takes a callable
    # reduce_fn and a list of votes from each worker. The method returns the consensus
