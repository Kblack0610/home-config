# windows-playtest-target

Turns a Windows PC into a place game builds can be pushed to and started on screen, from the workstation, without anyone touching the PC.

- `C:\Playtest\` is the landing directory. Push scripts put each build in `C:\Playtest\<game>\<stamp>\` and write the exe's full path into `C:\Playtest\launch-target.txt`.
- `C:\Playtest\launch.ps1` stops any running copy of that exe and starts it.
- Scheduled task `playtest-launch` runs `launch.ps1` as `playtest_user` with an interactive token, so the game appears on the signed-in desktop. An SSH session is session 0 and cannot draw on the screen; `schtasks /run /tn playtest-launch` from SSH is how a push puts the game on the TV.

The task only runs while `playtest_user` is signed in at the PC. If nobody is signed in, `schtasks /run` succeeds and nothing appears.

The dodginballs push script is `scripts/builds/playtest-push.sh` in that repo.
