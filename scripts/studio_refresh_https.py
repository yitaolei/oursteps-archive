#!/usr/bin/env python3
"""Refresh only known Studio HTTPS DocumentRoot paths to immutable local release."""
import re,shutil,subprocess
from pathlib import Path
root=Path(__file__).resolve().parents[1];release=(root/'public-site/current').resolve(strict=True)
assert release.parent==(root/'public-site/releases').resolve(strict=True)
assert (release/'index.html').is_file()
base=Path.home()/'Library/Application Support/OurSteps/studio-apache-private'
for name in ('httpd.conf','httpd-auth-test.conf'):
 p=base/name
 if not p.is_file():continue
 old=p.read_text()
 new=re.sub(r'(?<=DocumentRoot ")[^"]+/public-site/releases/[0-9a-f]{64}(?=")',str(release),old)
 new=re.sub(r'(?<=<Directory ")[^"]+/public-site/releases/[0-9a-f]{64}(?=">)',str(release),new)
 if old==new:continue
 backup=base/(name+'.before-release-refresh');shutil.copy2(p,backup);p.write_text(new)
 test=subprocess.run(['/usr/sbin/httpd','-f',str(p),'-t'],capture_output=True,text=True)
 if test.returncode:shutil.copy2(backup,p);raise SystemExit(test.stderr)
 subprocess.run(['/usr/sbin/httpd','-f',str(p),'-k','graceful'],check=True,timeout=15)
print('STUDIO_HTTPS_REFRESH_OK',release.name)
