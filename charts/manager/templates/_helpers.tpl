{{/*
_helpers.tpl — Named templates for the HPC Pilot Manager chart.
*/}}

{{/*
Expand the name of the chart.
*/}}
{{- define "manager.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Full name: release-chart (truncated to 63 chars).
When the release name already contains the chart name the chart suffix is
dropped to avoid redundant names like "manager-manager".
*/}}
{{- define "manager.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := include "manager.name" . }}
{{- if contains $name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}

{{/*
Common labels applied to every resource.
*/}}
{{- define "manager.labels" -}}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
app.kubernetes.io/name: {{ include "manager.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/*
Selector labels (stable subset used by Deployment/Service).
*/}}
{{- define "manager.selectorLabels" -}}
app.kubernetes.io/name: {{ include "manager.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/*
Name of the ServiceAccount to use.
*/}}
{{- define "manager.serviceAccountName" -}}
{{- .Values.namespace -}}-sa
{{- end }}

{{/*
Name of the Secret containing FLASK_SECRET_KEY.
When flask.existingSecret is set, that secret is used; otherwise the chart
creates its own secret named after the release.
*/}}
{{- define "manager.secretName" -}}
{{- if .Values.flask.existingSecret }}
{{- .Values.flask.existingSecret }}
{{- else }}
{{- include "manager.fullname" . }}
{{- end }}
{{- end }}

{{/*
Name of the PVC for saved-deployment data.
*/}}
{{- define "manager.pvcName" -}}
{{- if and .Values.persistence.enabled .Values.persistence.existingClaim }}
{{- .Values.persistence.existingClaim }}
{{- else }}
{{- printf "%s-data" (include "manager.fullname" .) }}
{{- end }}
{{- end }}
