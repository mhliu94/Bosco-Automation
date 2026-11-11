# Export or renew a physical-phone session

The existing WJY export is ready to move to Linux. Enrollment is needed again only if the broker invalidates the reconnect session, the account/device changes, or the app protocol changes. Runtime trading does not use the phone.

The adapter currently supports BSL `com.tsci.hms` version 1.0.6 on the user's rooted Android phone. Open BSL, sign in, and select the intended account. Complete any broker verification on the phone. The selected account must match the login response's default account; export refuses an inconsistent account selection.

Install the optional enrollment tools in a separate or existing development environment:

```bash
python -m pip install '.[enroll]'
adb devices
```

Start a matching official [Frida Android server](https://frida.re/docs/android/) on the authorized rooted phone for this enrollment. Frida client/server versions must match; enrollment here used 17.26.2. The script attaches to the already running app, reads only its active account's session/device metadata, detaches, and writes private files. It does not perform login or submit financial requests.

Example (replace the paths/serial for your enrollment host):

```bash
python scripts/export_phone.py \
  --serial YOUR_PHYSICAL_PHONE_SERIAL \
  --output /path/to/KTrader/Bosco/WJY \
  --account-id BOSCO-WJY \
  --account-num-id 29 \
  --password-file /path/to/KTrader/Bosco/WJY/passwords.txt
```

The password file format is `trading_password=...` on its own line. An optional `account_password=...` line is ignored by the runtime export. Password values are read from the file, not command-line arguments, and not printed. If no password file is supplied on renewal, the existing exported trading password is preserved. A different account's credentials are never overwritten.

The output directory receives **only `credentials.json`**. To also generate initial settings, add `--config-output /path/to/Bosco-Automation/config/bosco.config.json --bootstrap YOUR_KAFKA_HOST:9092`. The config destination must be outside the credentials directory. An existing config is preserved. Generated settings point to the credential file and place state/logs under `../work/<account-id>/state` and `../logs/<account-id>` relative to the config directory.

Stop and remove the temporary Frida server after enrollment. Move credentials to the Linux credentials directory, and keep configuration and existing runtime state separately in the project. Keep those files private; renewed trading tokens are atomically written to `credentials.json`, so it must be writable by the service user. The exported reconnect session is not a promise of indefinite access: broker policy can revoke it at any time.

After renewal, run `bosco session-check` and `bosco authenticate`, then restart the worker. Successful account reporting clears the old authentication-required marker.
