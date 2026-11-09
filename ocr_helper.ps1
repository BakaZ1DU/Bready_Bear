param(
    [Parameter(Mandatory=$true)][string]$Path
)

Add-Type -AssemblyName System.Runtime.WindowsRuntime
[Windows.Storage.StorageFile,Windows.Storage,ContentType=WindowsRuntime] | Out-Null
[Windows.Graphics.Imaging.BitmapDecoder,Windows.Graphics.Imaging,ContentType=WindowsRuntime] | Out-Null
[Windows.Media.Ocr.OcrEngine,Windows.Media.Ocr,ContentType=WindowsRuntime] | Out-Null

$asTask = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
    $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
})[0]

function Await($op, $type) {
    $task = $asTask.MakeGenericMethod($type).Invoke($null, @($op))
    $task.Wait()
    return $task.Result
}

$file = Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync($Path)) ([Windows.Storage.StorageFile])
$stream = Await ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
$decoder = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
$bitmap = Await ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
$engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()
if (-not $engine) {
    throw "Windows OCR engine is not available"
}
$result = Await ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])

$lines = @()
foreach ($line in $result.Lines) {
    $xs = @()
    $ys = @()
    $xe = @()
    $ye = @()
    foreach ($word in $line.Words) {
        $xs += $word.BoundingRect.X
        $ys += $word.BoundingRect.Y
        $xe += ($word.BoundingRect.X + $word.BoundingRect.Width)
        $ye += ($word.BoundingRect.Y + $word.BoundingRect.Height)
    }
    if ($xs.Count -eq 0) { continue }
    $lines += [pscustomobject]@{
        text = $line.Text
        x = [int]([Linq.Enumerable]::Min([int[]]$xs))
        y = [int]([Linq.Enumerable]::Min([int[]]$ys))
        w = [int]([Linq.Enumerable]::Max([int[]]$xe) - [Linq.Enumerable]::Min([int[]]$xs))
        h = [int]([Linq.Enumerable]::Max([int[]]$ye) - [Linq.Enumerable]::Min([int[]]$ys))
    }
}

ConvertTo-Json -InputObject $lines -Compress
