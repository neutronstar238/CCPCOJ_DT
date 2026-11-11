"""Remote installers invoked by Ansible; CSGArena itself is not modified."""

LINUX_SCRIPT = r'''
import json, os, pathlib, re, socket, subprocess, sys, tempfile, time, urllib.request
c=json.loads(sys.argv[1]); binary=sys.argv[2]
base=pathlib.Path('/opt/csgarena-deploy'); role=c['role']; endpoint=c['endpoint']
def run(*args):
    return subprocess.run(args,check=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True).stdout
def active(name):
    return subprocess.run(['systemctl','is-active','--quiet',name]).returncode==0
def status():
    units=['csgarenad.service'] if role=='server' else ['csgarena.service','csgarena-watch.service']
    result={'role':role,'services':{u:active(u) for u in units}}
    if role=='server':
        try:
            with socket.create_connection(('127.0.0.1',endpoint['port']),3): pass
            result['listening']=True
        except OSError: result['listening']=False
    print(json.dumps(result)); return result
if c['action']=='status':
    status(); sys.exit(0)
if os.geteuid()!=0: sys.exit('Installation needs root elevation')
if role=='server':
    data=base/'server-data'; data.mkdir(mode=0o700,exist_ok=True)
    cfg=data/'server.json'
    if cfg.exists():
        saved=json.loads(cfg.read_text())
        if saved.get('listen_port',39721)!=endpoint['port'] or saved.get('scheme','https')!=endpoint['scheme']:
            sys.exit('Existing server endpoint differs; preserve data and update settings before retrying')
    else:
        cfg.write_text(json.dumps({'listen_port':endpoint['port'],'scheme':endpoint['scheme'],'enroll_open':True})); cfg.chmod(0o600)
    unit=pathlib.Path('/etc/systemd/system/csgarenad.service')
    if unit.exists() and 'CSGOJ deployment console' not in unit.read_text():
        sys.exit('Unmanaged csgarenad unit exists; refusing to overwrite')
    unit.write_text('[Unit]\nDescription=CSGArena server - CSGOJ deployment console\nAfter=network-online.target\n[Service]\nExecStart='+binary+' --data-dir '+str(data)+'\nRestart=on-failure\nRestartSec=3\nUMask=0077\n[Install]\nWantedBy=multi-user.target\n')
    run('systemctl','daemon-reload'); run('systemctl','enable','csgarenad.service'); run('systemctl','restart','csgarenad.service')
    for _ in range(30):
        try:
            with socket.create_connection(('127.0.0.1',endpoint['port']),1): pass
            break
        except OSError: time.sleep(1)
    else: sys.exit('Server did not listen within 30 seconds; check journalctl -u csgarenad')
else:
    receipt=base/'client-enrollment.json'
    if receipt.exists():
        previous=json.loads(receipt.read_text())
        if previous['endpoint']!=endpoint: sys.exit('Client already belongs to another server; rebind through maintenance UI')
        if previous['binary']!=binary: sys.exit('Client already installed; use the product upgrade workflow, not fresh enrollment')
        if not all(status()['services'].values()): sys.exit('Previously enrolled client is not running; inspect its services')
        print('Existing enrollment preserved'); sys.exit(0)
    if any(pathlib.Path(p).exists() for p in ['/etc/csgarena/machine_id','/var/lib/csgarena/state.sealed','/var/lib/csgarena/bin/state.sealed','/usr/local/lib/csgarena/csgarena']):
        sys.exit('Existing client identity found; use maintenance UI rather than overwriting it')
    with tempfile.TemporaryFile(mode='w+b') as log:
        p=subprocess.Popen([binary,'setup'],stdout=log,stderr=log)
        try:
            url=None
            for _ in range(90):
                log.seek(0); output=log.read().decode('utf-8','replace')
                match=re.search(r'^http://127\.0\.0\.1:\d+/$',output,re.M)
                if match: url=match.group(0); break
                if p.poll() is not None: sys.exit('Setup exited before opening its wizard; inspect CSGArena installation prerequisites')
                time.sleep(1)
            if not url: sys.exit('Setup wizard timed out')
            def post(path,body):
                req=urllib.request.Request(url+path,data=json.dumps(body).encode(),headers={'Content-Type':'application/json'})
                with urllib.request.urlopen(req,timeout=120) as r: return json.load(r)
            probe=post('api/probe',{'address':endpoint['scheme']+'://'+endpoint['host'],'port':str(endpoint['port'])})
            if not probe.get('hit'): sys.exit('Cannot probe configured server; check server and firewall')
            result=post('api/enroll',{'hit':probe['hit'],'sudo':''})
            if not result.get('enrolled') or not result.get('installed'):
                sys.exit('Enrollment or autostart failed; inspect client local wizard. Partial identity is preserved.')
            receipt.write_text(json.dumps({'endpoint':endpoint,'binary':binary})); receipt.chmod(0o600)
        finally:
            if p.poll() is None:
                p.terminate()
                try: p.wait(timeout=10)
                except subprocess.TimeoutExpired: p.kill(); p.wait()
r=status()
if not all(r['services'].values()): sys.exit('Installed but a service is not active')
print('Installation verified; client heartbeat and exam policies require venue acceptance testing')
'''


WINDOWS_SCRIPT = r'''
param([string]$ConfigJson,[string]$Binary)
$ErrorActionPreference='Stop'
$c=$ConfigJson | ConvertFrom-Json
$base='C:\ProgramData\CSGArenaDeploy'
$taskName='CSGArenaServer-DeployConsole'
function Get-ArenaStatus {
    if ($c.role -eq 'server') {
        $task=Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
        $listening=@(Get-NetTCPConnection -State Listen -LocalPort $c.endpoint.port -ErrorAction SilentlyContinue).Count -gt 0
        return @{role='server';running=($null -ne $task -and $task.State -eq 'Running');listening=$listening}
    }
    $main=Get-Service CSGArena -ErrorAction SilentlyContinue
    $watch=Get-Service CSGArenaWatch -ErrorAction SilentlyContinue
    return @{role='client';running=($null -ne $main -and $main.Status -eq 'Running');watchRunning=($null -ne $watch -and $watch.Status -eq 'Running')}
}
if ($c.action -eq 'status') { Get-ArenaStatus | ConvertTo-Json -Compress; return }
$identity=[Security.Principal.WindowsIdentity]::GetCurrent()
$principal=New-Object Security.Principal.WindowsPrincipal($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw 'Use an administrative WinRM account with an elevated token' }
if ($c.role -eq 'server') {
    $data=Join-Path $base 'server-data'
    New-Item -ItemType Directory -Path $data -Force | Out-Null
    # Server data contains private keys and credentials; only SYSTEM and administrators.
    & icacls.exe $base /inheritance:r /grant:r '*S-1-5-18:(OI)(CI)F' '*S-1-5-32-544:(OI)(CI)F' | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Cannot protect server data ACL' }
    $cfg=Join-Path $data 'server.json'
    if (Test-Path $cfg) {
        $saved=Get-Content -Raw $cfg | ConvertFrom-Json
        $port=if ($saved.listen_port) {$saved.listen_port} else {39721}
        $scheme=if ($saved.scheme) {$saved.scheme} else {'https'}
        if ($port -ne $c.endpoint.port -or $scheme -ne $c.endpoint.scheme) { throw 'Existing server endpoint differs; preserve data and update settings before retrying' }
    } else {
        $json=@{listen_port=$c.endpoint.port;scheme=$c.endpoint.scheme;enroll_open=$true} | ConvertTo-Json
        [IO.File]::WriteAllText($cfg,$json,(New-Object Text.UTF8Encoding($false)))
    }
    $old=Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    if ($old -and $old.Description -ne 'Managed by CSGOJ deployment console') { throw 'Unmanaged task exists; refusing to overwrite' }
    if ($old) { Stop-ScheduledTask -TaskName $taskName; Start-Sleep -Seconds 2 }
    $action=New-ScheduledTaskAction -Execute $Binary -Argument ('--data-dir "'+$data+'"')
    $trigger=New-ScheduledTaskTrigger -AtStartup
    $settings=New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
    Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings -User SYSTEM -RunLevel Highest -Description 'Managed by CSGOJ deployment console' -Force | Out-Null
    Start-ScheduledTask -TaskName $taskName
    for ($i=0;$i -lt 30;$i++) { $state=Get-ArenaStatus; if ($state.running -and $state.listening) {break}; Start-Sleep -Seconds 1 }
    if (-not ($state.running -and $state.listening)) { throw 'Server did not start/listen within 30 seconds' }
} else {
    $receipt=Join-Path $base 'client-enrollment.json'
    if (Test-Path $receipt) {
        $previous=Get-Content -Raw $receipt | ConvertFrom-Json
        if ($previous.endpoint.host -ne $c.endpoint.host -or $previous.endpoint.port -ne $c.endpoint.port -or $previous.endpoint.scheme -ne $c.endpoint.scheme) { throw 'Already bound to another server; use maintenance UI' }
        if ($previous.binary -ne $Binary) { throw 'Already installed; use product upgrade workflow' }
        $state=Get-ArenaStatus
        if (-not ($state.running -and $state.watchRunning)) { throw 'Previously enrolled client services are not running' }
        'Existing enrollment preserved'; return
    }
    if ((Test-Path 'C:\ProgramData\CSGArena\machine_id') -or (Test-Path 'C:\ProgramData\CSGArena\state.sealed') -or (Test-Path 'C:\ProgramData\CSGArena\bin\state.sealed') -or (Test-Path 'C:\Program Files\CSGArena\csgarena.exe')) { throw 'Existing client installation found; use maintenance UI' }
    $out=Join-Path $base 'setup.stdout'; $err=Join-Path $base 'setup.stderr'
    $p=$null
    try {
        $p=Start-Process -FilePath $Binary -ArgumentList 'setup' -WindowStyle Hidden -RedirectStandardOutput $out -RedirectStandardError $err -PassThru
        $url=$null
        for ($i=0;$i -lt 90;$i++) {
            if (Test-Path $out) {
                $text=Get-Content -Raw $out
                if ($text -match '(?m)^http://127\.0\.0\.1:\d+/\r?$') { $url=$Matches[0].Trim(); break }
            }
            if ($p.HasExited) { throw 'Setup exited before opening wizard; check installation prerequisites' }
            Start-Sleep -Seconds 1
        }
        if (-not $url) { throw 'Setup wizard timed out' }
        $probeBody=@{address=($c.endpoint.scheme+'://'+$c.endpoint.host);port=[string]$c.endpoint.port} | ConvertTo-Json
        $probe=Invoke-RestMethod -Uri ($url+'api/probe') -Method Post -ContentType 'application/json' -Body $probeBody -TimeoutSec 120
        if (-not $probe.hit) { throw 'Server probe failed; check server and firewall' }
        $body=@{hit=$probe.hit;sudo=''} | ConvertTo-Json -Depth 8
        $result=Invoke-RestMethod -Uri ($url+'api/enroll') -Method Post -ContentType 'application/json' -Body $body -TimeoutSec 120
        if (-not $result.enrolled -or -not $result.installed) { throw 'Enrollment or autostart failed; partial identity preserved for maintenance' }
        $json=@{endpoint=$c.endpoint;binary=$Binary} | ConvertTo-Json
        [IO.File]::WriteAllText($receipt,$json,(New-Object Text.UTF8Encoding($false)))
    } finally {
        if ($null -ne $p -and -not $p.HasExited) { Stop-Process -Id $p.Id -Force }
        Remove-Item -LiteralPath $out,$err -Force -ErrorAction SilentlyContinue
    }
    $state=Get-ArenaStatus
    if (-not ($state.running -and $state.watchRunning)) { throw 'Installed but client services are not running' }
}
$state | ConvertTo-Json -Compress
'''
