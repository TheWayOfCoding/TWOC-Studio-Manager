# SPDX-License-Identifier: AGPL-3.0-or-later
# One-time: put the Hub on HTTPS (self-signed LAN cert) so browser features
# that require a secure context (mic input) work from other PCs.
# Key + cert live in C:\studio\tls (outside the repo tree: deploys never
# touch or ship them). Trust is by browser warning (verify against the
# printed fingerprint); the kiosk uses --ignore-certificate-errors on
# localhost. Safe to re-run. Run ONCE on the hub:
#   powershell -NoProfile -ExecutionPolicy Bypass -File C:\studio\hub-tls.ps1
# (this file ships via deploy; or scp it over and run it).
# -Regenerate rotates the keypair (backs the old pair up as .prev first).
# Any device that trusted the old cert must install the new one after.
# -HubIP sets the LAN SAN explicitly (default: auto-detect the hub's own
# primary LAN IPv4, so no address is ever hardcoded here):
#   hub-tls.ps1 -HubIP 192.0.2.10
param([switch]$Regenerate, [string]$HubIP)
$ErrorActionPreference = "Stop"
$HubRoot = "C:\studio\studio-hub"
$TlsDir = "C:\studio\tls"
$VenvPy = "$HubRoot\.venv\Scripts\python.exe"
if (-not $HubIP) {
    $HubIP = (Get-CimInstance Win32_NetworkAdapterConfiguration -Filter "IPEnabled = TRUE").IPAddress |
        Where-Object { $_ -match '^\d+\.\d+\.\d+\.\d+$' -and $_ -notlike '127.*' -and $_ -notlike '169.254.*' } |
        Select-Object -First 1
    if (-not $HubIP) { throw "no LAN IPv4 found; pass -HubIP <addr>" }
}
try { $null = [ipaddress]$HubIP } catch { throw "not an IPv4 address: $HubIP" }
Write-Host "LAN SAN: $HubIP"

# 1. cryptography (one-shot cert generation only; not a hub runtime dep).
& $VenvPy -c "import cryptography" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "installing cryptography (one-time)"
    & $VenvPy -m pip install -q --disable-pip-version-check cryptography
    if ($LASTEXITCODE -ne 0) { throw "pip install cryptography failed" }
}

# 2. Generate key + self-signed CA cert (LAN IP + localhost SANs, 10 years).
# A proper CA (BasicConstraints CA:true) so Android's CA installer accepts
# it; Windows also trusts it from the Root store. Skipped when a pair
# already exists, so re-runs never invalidate trust (unless -Regenerate).
New-Item -ItemType Directory -Force $TlsDir | Out-Null
$havePair = (Test-Path "$TlsDir\hub.key") -and (Test-Path "$TlsDir\hub.crt")
if ($havePair -and -not $Regenerate) { Write-Host "keypair exists, keeping it" } else {
if ($Regenerate -and $havePair) {
    Copy-Item "$TlsDir\hub.key" "$TlsDir\hub.key.prev" -Force
    Copy-Item "$TlsDir\hub.crt" "$TlsDir\hub.crt.prev" -Force
    Write-Host "old pair backed up as .prev"
}
$gen = @'
import datetime
import ipaddress
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "studio-hub")])
now = datetime.datetime.now(datetime.timezone.utc)
cert = (
    x509.CertificateBuilder()
    .subject_name(name).issuer_name(name)
    .public_key(key.public_key())
    .serial_number(x509.random_serial_number())
    .not_valid_before(now - datetime.timedelta(days=1))
    .not_valid_after(now + datetime.timedelta(days=3650))
    .add_extension(x509.SubjectAlternativeName([
        x509.IPAddress(ipaddress.ip_address("%%HUBIP%%")),
        x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
        x509.DNSName("localhost"),
        x509.DNSName("studio-hub"),
    ]), critical=False)
    .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
    .add_extension(x509.KeyUsage(digital_signature=True, key_cert_sign=True,
        crl_sign=True, content_commitment=False, key_encipherment=False,
        data_encipherment=False, key_agreement=False, encipher_only=False,
        decipher_only=False), critical=True)
    .sign(key, hashes.SHA256())
)
open("C:/studio/tls/hub.key", "wb").write(key.private_bytes(
    serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL,
    serialization.NoEncryption()))
open("C:/studio/tls/hub.crt", "wb").write(cert.public_bytes(serialization.Encoding.PEM))
print("fingerprint sha256:", cert.fingerprint(hashes.SHA256()).hex(":"))
'@
$gen = $gen.Replace("%%HUBIP%%", $HubIP)
Set-Content "$TlsDir\gen-cert.py" $gen -NoNewline
& $VenvPy "$TlsDir\gen-cert.py"
if ($LASTEXITCODE -ne 0) { throw "cert generation failed" }
Remove-Item "$TlsDir\gen-cert.py" -Force
} # end else (fresh keypair)

# 3. No trust install here: Root writes are blocked over non-interactive
# SSH. Trust is installed per device instead: Windows via
#   certutil -addstore Root <hub.crt copy>   (admin shell)
# and Android via Settings > Install certificates > CA certificate.
# Verify against the fingerprint printed at the end of this script.
# The kiosk uses --ignore-certificate-errors (localhost only).

# 4. Point the StudioHub task at the dual-listener runner (mirrors
# win-bootstrap.ps1; hub-serve.py reads this TLS pair itself and adds
# plain HTTP on 8001 alongside HTTPS on 8000).
$tr = "cmd /c cd /d $HubRoot && .venv\Scripts\python.exe tools\hub-serve.py >> logs\hub-task.log 2>&1"
schtasks /Change /TN StudioHub /TR "$tr" | Out-Null
if ($LASTEXITCODE -ne 0) { throw "schtasks /Change failed" }
schtasks /End /TN StudioHub 2>$null | Out-Null
Start-Sleep 3
schtasks /Run /TN StudioHub | Out-Null

# 5. Kiosk bat: rewrite for plain local HTTP via the kiosk helper (no cert
# friction ever on localhost; HTTPS stays the LAN dashboard door).
# Keeps the current screen id when the bat already names one.
$startup = Join-Path ([Environment]::GetFolderPath("Startup")) "studio-kiosk.bat"
$kioskUrl = "http://localhost:8001/screen/kqc-screen"
if (Test-Path $startup) {
    $old = Get-Content $startup -Raw
    $found = [regex]::Match($old, "/screen/(\S+)")
    if ($found.Success) { $kioskUrl = "http://localhost:8001/screen/" + $found.Groups[1].Value }
}
& $VenvPy "$HubRoot\tools\hub-kiosk.py" install $kioskUrl
if ($LASTEXITCODE -ne 0) { Write-Warning "kiosk reinstall failed (fails when nobody is logged on at the glass); rerun this script later" }

# 6. Health over both listeners via curl (.NET Framework TLS cannot
# handshake here, so curl covers https too).
$health = $null
for ($i = 0; $i -lt 12; $i++) {
    foreach ($probe in @("https://127.0.0.1:8000", "http://127.0.0.1:8001")) {
        try {
            $raw = curl.exe -sk --max-time 3 "$probe/api/health" 2>$null
            if ($raw) { $health = $raw | ConvertFrom-Json; break }
        } catch { }
    }
    if ($health) { break }
    Start-Sleep 5
}
if (-not $health) { throw "Hub did not answer on either listener; see $HubRoot\logs\hub-task.log" }
$health | ConvertTo-Json
& $VenvPy -c "from cryptography import x509; from cryptography.hazmat.primitives import hashes; c=x509.load_pem_x509_certificate(open('C:/studio/tls/hub.crt','rb').read()); print('fingerprint sha256:', c.fingerprint(hashes.SHA256()).hex(':')); print('CA:', c.extensions.get_extension_for_class(x509.BasicConstraints).value.ca)"
Write-Host "TLS_OK"
