This is a great idea. Stepping away is often exactly what a complex DevOps problem needs. Here is a complete debrief of our troubleshooting session, the root cause we uncovered, and the exact steps to take when you pick this back up.

---

## 1. The Goal

To use **Ansible** to deploy user-space tools (`wstunnel`, `supervisord`) to remote HPC nodes. Authentication must be handled via **`motley-cue` / `mccli**`, which dynamically maps an OIDC token to a local Unix user (e.g., `nogroup001`).

## 2. The Trials & Errors

We attempted to force Ansible to use an `mccli` wrapper script (`exec mccli ssh "$@"`) to handle the OIDC token injection natively. We hit a cascading series of edge cases:

1. **The EOF Error:** Ansible's default SSH arguments disabled keyboard-interactive authentication. *Fix: We overrode the defaults to force `KbdInteractiveAuthentication=yes`.*
2. **The Bash Syntax Error:** Ansible's standard file-transfer method (`sftp`/`scp`) sends complex, nested shell commands. `mccli` stripped the quotes from these commands, causing Bash to choke on unexpected parentheses. *Fix: We enabled Ansible Pipelining to bypass the SCP file-transfer phase entirely.*
3. **The Pipelining Timeout:** Pipelining runs silently without a TTY. Because there was no TTY, the remote server skipped the interactive PAM prompt for the token. `mccli` hung forever waiting for an "Access Token:" prompt. *Fix: We appended `-tt` to the SSH arguments to force a TTY.*
4. **The Mangled Path Error:** Forcing a TTY caused the SSH server to print diagnostic artifacts (like *"Shared connection closed"*) to standard output. Ansible read these artifacts as part of the directory path and crashed. *Fix: We appended `-q` (quiet mode) to suppress the connection artifacts.*
5. **The Empty String Error:** The final boss. The command succeeded, but Ansible received a completely empty output buffer (`b''`) and crashed while trying to parse the JSON response.

## 3. The Root Cause: The "TTY Paradox"

The core issue is a fundamental incompatibility in how data streams are handled by the two tools:

* **`mccli` (via Python's `pexpect`)** *requires* a pseudo-TTY to emulate a human typing so it can intercept the PAM prompt and inject your OIDC token. However, when the session closes, `pexpect` swallows the standard output buffer with it.
* **Ansible** *requires* a pristine, non-interactive, non-TTY data stream to pipeline its Python code and parse the returning JSON data.

**Conclusion:** You cannot wrap Ansible's execution loop inside `mccli`. `mccli` needs a TTY to authenticate; Ansible needs the absence of a TTY to execute.

---

## 4. Next Steps (The "Bootstrap" Method)

When you return, we will abandon the wrapper. Instead, we will decouple the authentication phase from the execution phase.

You will use `mccli` strictly as a one-time setup tool to discover your dynamic username and inject a standard SSH key. Once the key is in place, you hand the deployment over to native, blazing-fast Ansible.

**1. Revert your `ansible.cfg` to native settings (no wrapper):**

```ini
[defaults]
inventory = ./hosts.ini
host_key_checking = False
stdout_callback = yaml

[ssh_connection]
pipelining = True
transfer_method = smart
ssh_args = -C -o ControlMaster=auto -o ControlPersist=60m

```

**2. Run this "Bootstrap" script in your terminal when you are ready to deploy:**

```bash
# 1. Ask motley-cue for your dynamically mapped username
export OIDC_USER=$(mccli ssh 161.9.255.206 -p 3333 whoami)
echo "motley-cue mapped you to: $OIDC_USER"

# 2. Inject your local SSH public key using mccli
# (Change id_ed25519.pub to id_rsa.pub depending on your key type)
mccli ssh 161.9.255.206 -p 3333 "mkdir -p ~/.ssh && chmod 700 ~/.ssh && echo \"$(cat ~/.ssh/id_ed25519.pub)\" >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys"

# 3. Run pure, native Ansible using that dynamic username
ansible all -m ping -u $OIDC_USER

```

This bypasses all quoting bugs, timeouts, and empty buffers. Good luck with your other hat, and this will be ready for you when you get back!
