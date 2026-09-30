"""In-memory, per-`iss` runtime overrides for Service-enacted config.

The file `InstanceConfig` (mounted Secret/ConfigMap) is the *default*; the benchmarker
config API can override the Service-enacted parts (MLflow, S3) at runtime. This overlay
is intentionally ephemeral (lost on restart), matching the documented "file default +
benchmarker override" model. It never holds workload-provided credentials — those live as
cluster Secrets the Service never touches.
"""

from .models import ConfigResponse, ConfigUpdateRequest, InstanceConfig, MLflowConfig, S3Config

_REDACT_MARKERS = ("secret", "password", "token")
_REDACTED = "***"


def _redact(model: MLflowConfig | S3Config):
    """Return a copy with any set field whose name looks secret masked to '***'.

    `token_url`/`tracking_url` are URLs, not secrets — only mask fields whose name
    contains a secret marker as a whole word-ish suffix (client_secret, secret_access_key,
    password), not merely because they contain 'token'/'url'.
    """
    data = model.model_dump()
    masked = {
        k: (_REDACTED if v is not None and _is_secret_field(k) else v)
        for k, v in data.items()
    }
    return type(model)(**masked)


def _is_secret_field(name: str) -> bool:
    return name in ("secret_access_key", "client_secret", "password", "bearer_token")


class RuntimeConfigStore:
    def __init__(self) -> None:
        self._overrides: dict[str, dict] = {}

    def apply(self, iss: str, update: ConfigUpdateRequest) -> None:
        """Field-level merge of the provided mlflow/s3 sub-objects into the iss override.

        `exclude_unset`, NOT `exclude_none` — the distinction is the whole correctness of this
        method. `exclude_none` drops only fields that are None, so every field whose *default* is
        something else got written on every call, whether or not the caller mentioned it. Three
        fields have non-None defaults, and each one fails silently:

        - `MLflowConfig.experiment_id` (`"0"`) — so `PUT {"mlflow": {"tracking_url": ...}}` also
          repointed the Service at experiment 0. It then emits spans under one experiment id and
          reads the report back from another, and a run publishes an EMPTY report while reporting
          `pass_rate 1.0`. Observed live on ykt5 2026-09-30: a PUT of `insecure_tls` alone moved
          `experiment_id` from 1 to 0 and the next span export returned 404.
        - `MLflowConfig.insecure_tls` (`False`) — re-enables verification against a port-forwarded
          reencrypt endpoint that cannot satisfy it.
        - `S3Config.public_read` (`True`) — silently re-enables public-read ACLs on a bucket an
          operator had deliberately made private. The worst of the three, since nothing downstream
          reports it and the effect is on published objects.

        With `exclude_unset` an explicit `null` is now meaningful: `{"mlflow": {"workspace": null}}`
        clears the workspace, where before it was indistinguishable from not mentioning it. That is
        the useful reading of an explicit null, and there is no other way to unset a field.
        """
        current = self._overrides.setdefault(iss, {})
        for section in ("mlflow", "s3"):
            incoming = getattr(update, section)
            if incoming is None:
                continue
            merged = current.get(section, {})
            merged.update(incoming.model_dump(exclude_unset=True))
            current[section] = merged

    def effective(self, iss: str, base: InstanceConfig) -> InstanceConfig:
        """Return `base` with mlflow/s3 overlaid by the stored override for `iss`."""
        override = self._overrides.get(iss)
        if not override:
            return base
        return base.model_copy(
            update={
                "mlflow": base.mlflow.model_copy(update=override.get("mlflow", {})),
                "s3": base.s3.model_copy(update=override.get("s3", {})),
            }
        )

    def response(self, iss: str, base: InstanceConfig) -> ConfigResponse:
        """Effective Service-enacted config for `iss`, with secret fields redacted."""
        eff = self.effective(iss, base)
        return ConfigResponse(iss=iss, mlflow=_redact(eff.mlflow), s3=_redact(eff.s3))
