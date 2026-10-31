# 毎日17:00に note_post.bat を動かすタスクを登録する。PowerShell で一度だけ実行:
#   powershell -ExecutionPolicy Bypass -File .\note\post\install_task.ps1
# やめるとき: Unregister-ScheduledTask -TaskName "BoatRaceNotePost" -Confirm:$false
$bat = Join-Path $PSScriptRoot "note_post.bat"
$action = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c `"$bat`""
$trigger = New-ScheduledTaskTrigger -Daily -At 17:00
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 30)
Register-ScheduledTask -TaskName "BoatRaceNotePost" -Action $action -Trigger $trigger -Settings $settings `
  -Description "Note に明日のボートレース予想記事を投稿する" -Force
Write-Host "登録しました: 毎日 17:00 に $bat"
