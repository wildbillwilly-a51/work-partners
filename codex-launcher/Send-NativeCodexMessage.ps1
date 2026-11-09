[CmdletBinding(DefaultParameterSetName='Send')]
param(
    [Parameter(Mandatory)] [string] $PythonPath,
    [Parameter(Mandatory)] [string] $NodePath,
    [Parameter(Mandatory, ParameterSetName='Send')]
    [Parameter(ParameterSetName='Start')]
    [string] $HelperPath,
    [Parameter(Mandatory, ParameterSetName='Send')] [guid] $ThreadId,
    [Parameter(Mandatory, ParameterSetName='Send')] [guid] $AuthorityThread,
    [Parameter(Mandatory, ParameterSetName='Start')] [guid] $ContextThread,
    [Parameter(Mandatory, ParameterSetName='Start')] [string] $ProjectRoot,
    [Parameter(Mandatory)] [string] $PromptFile,
    [Parameter(Mandatory)] [string] $Receipt
)

$ErrorActionPreference = 'Stop'
$package = Get-AppxPackage -Name OpenAI.Codex
if (-not $package) { throw 'The Codex desktop package is unavailable.' }
$server = Join-Path $package.InstallLocation 'app/resources/plugins/openai-bundled/plugins/codex-app-tools/server.mjs'
if ($PSCmdlet.ParameterSetName -eq 'Start') {
    & $PythonPath -B (Join-Path $PSScriptRoot 'start-codex-chat.py') $NodePath $server ($ContextThread.ToString()) $ProjectRoot $PromptFile $Receipt
    exit $LASTEXITCODE
}
& $PythonPath -B $HelperPath --node $NodePath --server $server --context-thread ($ThreadId.ToString()) `
    --authority-thread ($AuthorityThread.ToString()) send ($ThreadId.ToString()) --prompt-file $PromptFile --receipt $Receipt --execute
exit $LASTEXITCODE
