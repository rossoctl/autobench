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
