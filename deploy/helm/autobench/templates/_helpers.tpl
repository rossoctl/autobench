{{/*
Object name. Fixed to autobench-service by default, because the Deployment's label selector is
IMMUTABLE: renaming it on an existing install is a create → verify → delete, not an upgrade.
*/}}
{{- define "autobench.name" -}}
{{- default "autobench-service" .Values.nameOverride -}}
{{- end -}}

{{/*
The one label on every object, and the ONLY selector label.

Deliberately NOT the standard app.kubernetes.io/* set: these objects exist on clusters that were
created from deploy/*.yaml, whose selector is `app: autobench-service`, and `helm template` output
is diffed against those manifests as the chart's correctness gate. Adding labels would break both.
*/}}
{{- define "autobench.labels" -}}
app: {{ include "autobench.name" . }}
{{- end -}}

{{- define "autobench.image" -}}
{{ .Values.image.repository }}:{{ .Values.image.tag | default .Chart.AppVersion }}
{{- end -}}

{{/*
Fail loudly on an unknown platform rather than silently rendering the wrong security context.
*/}}
{{- define "autobench.platform" -}}
{{- $p := .Values.platform -}}
{{- if not (or (eq $p "openshift") (eq $p "kind")) -}}
{{- fail (printf "platform must be \"openshift\" or \"kind\", got %q" $p) -}}
{{- end -}}
{{- $p -}}
{{- end -}}

{{/*
The declared LLM gateway profile. Empty is allowed — an install that predates the profiles, or one
whose gateway is set only in the instance file — but a typo is not: `intranett` would otherwise be
silently accepted here and then silently ignored by the bootstrap scripts, which is the failure mode
this whole mechanism exists to remove.
*/}}
{{- define "autobench.llmProfile" -}}
{{- $p := .Values.llmProfile | default "" -}}
{{- if not (or (eq $p "") (eq $p "intranet") (eq $p "internet")) -}}
{{- fail (printf "llmProfile must be \"intranet\", \"internet\" or empty, got %q" $p) -}}
{{- end -}}
{{- $p -}}
{{- end -}}
