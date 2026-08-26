$ErrorActionPreference = "Stop"
node (Join-Path $PSScriptRoot "start-web.mjs")
