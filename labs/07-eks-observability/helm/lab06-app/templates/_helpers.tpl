{{/*
Chart name, truncated to the 63-char DNS label limit.
*/}}
{{- define "lab06-app.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Fully qualified app name. Release name "lab06-app" + chart "lab06-app" collapses
to "lab06-app" (the usual Helm convention), otherwise <release>-<chart>.
*/}}
{{- define "lab06-app.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default .Chart.Name .Values.nameOverride }}
{{- if contains $name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}

{{- define "lab06-app.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Common labels (kubernetes.io recommended set).
*/}}
{{- define "lab06-app.labels" -}}
helm.sh/chart: {{ include "lab06-app.chart" . }}
{{ include "lab06-app.selectorLabels" . }}
app.kubernetes.io/version: {{ default .Chart.AppVersion .Values.image.tag | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/part-of: awslabs-lab07
{{- end }}

{{/*
Selector labels: immutable once a Deployment exists, so keep them minimal.
*/}}
{{- define "lab06-app.selectorLabels" -}}
app.kubernetes.io/name: {{ include "lab06-app.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{- define "lab06-app.serviceAccountName" -}}
{{- if .Values.serviceAccount.create }}
{{- default (include "lab06-app.fullname" .) .Values.serviceAccount.name }}
{{- else }}
{{- default "default" .Values.serviceAccount.name }}
{{- end }}
{{- end }}

{{/*
The image reference. Both halves are required: no silent "latest".
*/}}
{{- define "lab06-app.image" -}}
{{- $repo := required "image.repository is required (lab 06's ECR repository URL)" .Values.image.repository -}}
{{- $tag := required "image.tag is required (the git SHA lab 06 CI pushed)" .Values.image.tag -}}
{{- printf "%s:%s" $repo $tag -}}
{{- end }}
