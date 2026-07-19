param(
    [string]$OutputDirectory = "",
    [switch]$CheckOnly
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
$blogUrl = "https://person-of-ehomaki.blog.jp/"
if (-not $OutputDirectory) {
    $OutputDirectory = Join-Path (Split-Path $PSScriptRoot -Parent) "downloads"
}

Write-Host "[SlNicoLiveRec] Checking the official distribution page..."
$blog = Invoke-WebRequest -Uri $blogUrl -UseBasicParsing
$links = [regex]::Matches(
    $blog.Content,
    'href="(?<url>[^"]+)"[^>]*>[^<]*SlNicoLiveRec\s+V(?<version>[0-9.]+)[^<]*</a>',
    [Text.RegularExpressions.RegexOptions]::IgnoreCase
)
if ($links.Count -eq 0) {
    throw "The latest SlNicoLiveRec download link was not found on $blogUrl"
}

$candidates = foreach ($match in $links) {
    [pscustomobject]@{
        Version = [version]$match.Groups['version'].Value
        Url = [Net.WebUtility]::HtmlDecode($match.Groups['url'].Value)
    }
}
$latest = $candidates | Sort-Object Version -Descending | Select-Object -First 1
Write-Host "[SlNicoLiveRec] Latest version: $($latest.Version)"
Write-Host "[SlNicoLiveRec] Download page: $($latest.Url)"
if ($CheckOnly) {
    exit 0
}

$session = New-Object Microsoft.PowerShell.Commands.WebRequestSession
$driveMatch = [regex]::Match($latest.Url, 'drive\.google\.com/file/d/(?<id>[^/]+)', 'IgnoreCase')
if ($driveMatch.Success) {
    $build = $latest.Version.ToString().Replace('.', '')
    $fileName = "SlNicoLiveRec$build.zip"
    $downloadUrl = "https://drive.usercontent.google.com/download?id=$($driveMatch.Groups['id'].Value)&export=download&confirm=t"
    $postBody = $null
} else {
    $downloadPage = Invoke-WebRequest -Uri $latest.Url -WebSession $session -UseBasicParsing
    $fileMatch = [regex]::Match($downloadPage.Content, 'SlNicoLiveRec(?<build>\d+)\.zip', 'IgnoreCase')
    $tokenMatch = [regex]::Match($downloadPage.Content, 'name="token"\s+value="(?<token>[^"]+)"', 'IgnoreCase')
    if (-not $fileMatch.Success -or -not $tokenMatch.Success) {
        throw "The ZIP name or download token was not found: $($latest.Url)"
    }
    $fileName = $fileMatch.Value
    $downloadUrl = $latest.Url
    $postBody = @{ token = $tokenMatch.Groups['token'].Value; yes = 'Download' }
}

New-Item -ItemType Directory -Force -Path $OutputDirectory | Out-Null
$outputPath = Join-Path $OutputDirectory $fileName
if (Test-Path -LiteralPath $outputPath) {
    Write-Host "[SlNicoLiveRec] Already downloaded: $outputPath"
    exit 0
}
$temporaryPath = "$outputPath.part"
Write-Host "[SlNicoLiveRec] Downloading: $fileName"
try {
    if ($null -eq $postBody) {
        Invoke-WebRequest -Uri $downloadUrl -WebSession $session -UseBasicParsing -OutFile $temporaryPath
    } else {
        Invoke-WebRequest -Uri $downloadUrl -Method Post -Body $postBody -WebSession $session -UseBasicParsing -OutFile $temporaryPath
    }
    if ((Get-Item $temporaryPath).Length -lt 1MB) {
        throw "The downloaded file is too small."
    }
    Move-Item -Force -LiteralPath $temporaryPath -Destination $outputPath
} catch {
    Remove-Item -Force -LiteralPath $temporaryPath -ErrorAction SilentlyContinue
    throw
}
Write-Host "[SlNicoLiveRec] Saved: $outputPath"
