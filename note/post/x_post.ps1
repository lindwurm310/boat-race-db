# note 記事を公開したあと、X に告知を1件投稿する（X API v2 / OAuth 1.0a）。
# note_post.bat から呼ばれる: powershell -ExecutionPolicy Bypass -File x_post.ps1 -NoteUrl <URL> -Date <YYYY-MM-DD>
# キーは同じフォルダの x_keys.json（リポジトリには入れない）:
#   {"api_key":"...","api_secret":"...","access_token":"...","access_secret":"..."}
param([Parameter(Mandatory)][string]$NoteUrl, [Parameter(Mandatory)][string]$Date, [switch]$DryRun)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$here = Split-Path -Parent $MyInvocation.MyCommand.Path

$j = Invoke-RestMethod 'https://lindwurm310.github.io/boat-race-db/note/latest.json'
$d = [datetime]::ParseExact($Date, 'yyyy-MM-dd', $null)
$wd = '日月火水木金土'[[int]$d.DayOfWeek]
$text = @"
【$($d.Month)月$($d.Day)日($wd)の予想を公開しました】
55万レースのデータから、$($j.races)レースの本命と単勝$($j.picks)点を選びました。
$NoteUrl
#ボートレース #競艇予想
"@
if ($DryRun) { Write-Output $text; exit 0 }

$k = Get-Content (Join-Path $here 'x_keys.json') -Raw -Encoding UTF8 | ConvertFrom-Json
function Enc([string]$s) { [Uri]::EscapeDataString($s) }
$url = 'https://api.x.com/2/tweets'
$oauth = [ordered]@{
  oauth_consumer_key     = $k.api_key
  oauth_nonce            = [guid]::NewGuid().ToString('N')
  oauth_signature_method = 'HMAC-SHA1'
  oauth_timestamp        = [string][DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
  oauth_token            = $k.access_token
  oauth_version          = '1.0'
}
# JSON 本文は署名に含めない（OAuth 1.0a の仕様）
$params = ($oauth.Keys | Sort-Object | ForEach-Object { "$(Enc $_)=$(Enc $oauth[$_])" }) -join '&'
$base = "POST&$(Enc $url)&$(Enc $params)"
$hmac = New-Object Security.Cryptography.HMACSHA1
$hmac.Key = [Text.Encoding]::ASCII.GetBytes("$(Enc $k.api_secret)&$(Enc $k.access_secret)")
$oauth['oauth_signature'] = [Convert]::ToBase64String($hmac.ComputeHash([Text.Encoding]::ASCII.GetBytes($base)))
$auth = 'OAuth ' + (($oauth.Keys | ForEach-Object { "$(Enc $_)=`"$(Enc $oauth[$_])`"" }) -join ', ')

$body = [Text.Encoding]::UTF8.GetBytes((@{ text = $text } | ConvertTo-Json -Compress))
try {
  $r = Invoke-RestMethod -Method Post -Uri $url -Headers @{ Authorization = $auth } -ContentType 'application/json; charset=utf-8' -Body $body
  Write-Output "XOK $Date https://x.com/i/web/status/$($r.data.id)"
} catch {
  Write-Output "XNG $Date $($_.Exception.Message) $($_.ErrorDetails.Message)"
  exit 1
}
