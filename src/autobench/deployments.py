"""What each live deployment's LLM calls go to — gateway label, model, key Secret.

A run cannot discover this on its own: it only invokes an agent that is already deployed, and the
Service has no Kubernetes access with which to read the pod's env back. So the deploy route records
it here and the run copies it into `RunState.llm`, which is how a published row learns which gateway
served it. In memory and per process, like the run registry: a Service restart between a deploy and
its run leaves the run's `llm` empty rather than guessed.
"""

from .models import DeploymentLLM

Key = tuple[str, str, str, str, str]  # iss, namespace, benchmark, agent, experiment


class DeploymentRecords:
    def __init__(self) -> None:
        self._by_key: dict[Key, DeploymentLLM] = {}

    def put(self, key: Key, llm: DeploymentLLM) -> None:
        self._by_key[key] = llm

    def get(self, key: Key) -> DeploymentLLM | None:
        return self._by_key.get(key)

    def drop(self, key: Key) -> None:
        self._by_key.pop(key, None)
