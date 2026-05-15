# armsim Phase A — Simulator

Phase A is the sim shipping today: a Rail HTTP handler renders an arm to
a browser viewer. Hardware control lives in
[PHASE_B_HARDWARE.md](./PHASE_B_HARDWARE.md).

## How it runs

- `serve.py` is a thin TCP loop. On each request it writes the raw HTTP
  bytes to `/tmp/armsim_req.txt`, execs the compiled Rail handler at
  `/tmp/armsim_handler`, and streams stdout back to the client.
- `src/armsim.rail` parses the request, dispatches by path, persists
  joint state to `/tmp/armsim_state.txt`, and emits the HTTP response.

## Routes

| Method | Path                  | Query                        | Result                      |
|--------|-----------------------|------------------------------|-----------------------------|
| GET    | `/`                   |                              | viewer HTML                 |
| GET    | `/state`              |                              | current joints + FK         |
| GET    | `/pose`               | `b=&s=&e=` (degrees)         | set joints, run FK          |
| GET    | `/reach`              | `x=&y=&z=` (millimeters)     | IK + clamp + run FK         |
| GET    | `/home`               |                              | reset to home pose          |
| GET    | `/poses`              |                              | list named poses            |
| GET    | `/poses/save`         | `name=`                      | snapshot current state      |
| GET    | `/poses/load`         | `name=`                      | restore named pose          |
| GET    | `/poses/delete`       | `name=`                      | remove named pose           |

## Known limitations (tracked for Phase A.5)

- Geometry is hardcoded for EEZYbotARM MK2 (l0/l1/l2, 3 DOF). The next
  refactor reads geometry from `arms/<name>.json`.
- Single-threaded handler (one process per request). Fine for the sim;
  hardware mode will likely need a long-running daemon.
- IK is elbow-up only; no wrist or end-effector orientation.
- `serve.py` is Python. Once `stdlib/http_server.rail` matures, the TCP
  loop should be Rail too.
