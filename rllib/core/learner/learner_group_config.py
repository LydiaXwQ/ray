from typing import Optional, Type, TYPE_CHECKING, Union

from ray.rllib.core.learner.learner import (
    LearnerSpec,
    LearnerHyperparameters,
    FrameworkHyperparameters,
)
from ray.rllib.core.learner.learner_group import LearnerGroup
from ray.rllib.core.learner.scaling_config import LearnerGroupScalingConfig
from ray.rllib.core.rl_module.marl_module import MultiAgentRLModuleSpec
from ray.rllib.core.rl_module.rl_module import SingleAgentRLModuleSpec
from ray.rllib.utils.from_config import NotProvided

if TYPE_CHECKING:
    from ray.rllib.core.rl_module.torch.torch_compile_config import TorchCompileConfig
    from ray.rllib.core.learner import Learner

ModuleSpec = Union[SingleAgentRLModuleSpec, MultiAgentRLModuleSpec]


# TODO (Kourosh, Sven): We should make all configs come from a standard base class that
#  defines the general interfaces for validation, from_dict, to_dict etc.
#  Also, all these classes should abide by the following design patterns:
#  - Define all default values for properties in the constructor.
#  - No properties are magically set under the hood, w/o the user calling one of its
#  setter methods (e.g. `.training()`). `validate()` is not one of these setter methods
#  and thus should never set any properties, only validate and warn/error.
#  - Any sub-configurations should be generated by calling a `.get_xyz_config()` method
#  and thus be compiled on-the-fly to avoid duplicate information.
class LearnerGroupConfig:
    """Configuration object for LearnerGroup."""

    def __init__(self, cls: Type[LearnerGroup] = None) -> None:

        # Define the default LearnerGroup class
        self.learner_group_class = cls or LearnerGroup

        # `self.module()`
        self.module_spec = None

        # `self.learner()`
        self.learner_class = None
        self.learner_hyperparameters = LearnerHyperparameters()

        # `self.resources()`
        self.num_gpus_per_learner_worker = 0
        self.num_cpus_per_learner_worker = 1
        self.num_learner_workers = 1

        # TODO (Avnishn): We should come back and revise how to specify algorithm
        # resources this is a stop gap solution for now so that users can specify the
        # local gpu id to use when training with gpu and local mode. I doubt this will
        # be used much since users who have multiple gpus will probably be fine with
        # using the 0th gpu or will use multi gpu training.
        self.local_gpu_idx = 0

        # `self.framework()`
        self.eager_tracing = True
        self.torch_compile_cfg = None

    def validate(self) -> None:

        if self.module_spec is None:
            raise ValueError(
                "Cannot initialize an Learner without the module specs. "
                "Please provide the specs via .module(module_spec)."
            )

        if self.learner_class is None:
            raise ValueError(
                "Cannot initialize an Learner without an Learner class. Please provide "
                "the Learner class with .learner(learner_class=MyLearnerClass)."
            )

    def build(self) -> LearnerGroup:
        self.validate()

        scaling_config = LearnerGroupScalingConfig(
            num_workers=self.num_learner_workers,
            num_gpus_per_worker=self.num_gpus_per_learner_worker,
            num_cpus_per_worker=self.num_cpus_per_learner_worker,
            local_gpu_idx=self.local_gpu_idx,
        )

        framework_hps = FrameworkHyperparameters(
            eager_tracing=self.eager_tracing,
            torch_compile_cfg=self.torch_compile_cfg,
        )

        learner_spec = LearnerSpec(
            learner_class=self.learner_class,
            module_spec=self.module_spec,
            learner_group_scaling_config=scaling_config,
            learner_hyperparameters=self.learner_hyperparameters,
            framework_hyperparameters=framework_hps,
        )

        return self.learner_group_class(learner_spec)

    def framework(
        self,
        eager_tracing: Optional[bool] = NotProvided,
        torch_compile_cfg: Optional["TorchCompileConfig"] = NotProvided,
    ) -> "LearnerGroupConfig":

        if eager_tracing is not NotProvided:
            self.eager_tracing = eager_tracing

        if torch_compile_cfg is not NotProvided:
            self.torch_compile_cfg = torch_compile_cfg

        return self

    def module(
        self,
        module_spec: Optional[ModuleSpec] = NotProvided,
    ) -> "LearnerGroupConfig":

        if module_spec is not NotProvided:
            self.module_spec = module_spec

        return self

    def resources(
        self,
        *,
        num_learner_workers: Optional[int] = NotProvided,
        num_gpus_per_learner_worker: Optional[int] = NotProvided,
        num_cpus_per_learner_worker: Optional[Union[float, int]] = NotProvided,
        local_gpu_idx: Optional[int] = NotProvided,
    ) -> "LearnerGroupConfig":

        if num_learner_workers is not NotProvided:
            self.num_learner_workers = num_learner_workers
        if num_gpus_per_learner_worker is not NotProvided:
            self.num_gpus_per_learner_worker = num_gpus_per_learner_worker
        if num_cpus_per_learner_worker is not NotProvided:
            self.num_cpus_per_learner_worker = num_cpus_per_learner_worker
        if local_gpu_idx is not NotProvided:
            self.local_gpu_idx = local_gpu_idx

        return self

    def learner(
        self,
        *,
        learner_class: Optional[Type["Learner"]] = NotProvided,
        learner_hyperparameters: Optional[LearnerHyperparameters] = NotProvided,
    ) -> "LearnerGroupConfig":

        if learner_class is not NotProvided:
            self.learner_class = learner_class
        if learner_hyperparameters is not NotProvided:
            self.learner_hyperparameters = learner_hyperparameters

        return self
