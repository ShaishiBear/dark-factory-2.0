# Front Door hosting payload — prepared, not activated

Target: the existing `dark-factory-runner` Lightsail instance in Ireland, last verified
at `108.131.113.253`, under the existing $7/month allowance. No instance, disk, static-IP
allocation, load balancer or other paid resource is created by these files.

The owner has no chosen hostname. Public-IP HTTPS is available through Let's Encrypt's
short-lived certificates. Use Certbot 5.4 or newer, an HTTP webroot challenge and automatic
renewal with a successful proxy reload hook. Certificates last about six days. Sources:
[Let's Encrypt IP certificate availability](https://letsencrypt.org/2026/01/15/6day-and-ip-general-availability)
and [Certbot support and renewal instructions](https://letsencrypt.org/2026/03/11/shorter-certs-certbot).
The ordinary Caddy IP default uses a local CA; it is not this public trust configuration.

## Isolation and activation prerequisites

- Keep the previous `/home/ubuntu/dark-factory-2.0` checkout, its STOP record and disabled
  supervisor services intact. Install a reviewed exact Git revision in a separate immutable
  release under `/opt/dark-factory-frontdoor`, then select it through `current`.
- Preserve the Git revision and committed MISSION/README/API facts needed by the context
  reader. An archive without its Git identity is not a complete service deployment.
- The service uses the already authenticated GitHub owner for observation and the fixed
  owner-stop dispatch. It contains no App private key. Actual stop effects use the protected
  workflow's freshly minted App identity.
- Create a private `0700` state directory at `/home/ubuntu/.local/state/dark-factory-frontdoor`
  with `home`, `config` and `intents` children. Generate the 32-byte owner bearer into the
  private `owner-token` file, encoded as 64 lowercase hex characters. Do not log or commit it.
- `service.env`, mode `0600`, initially needs only
  `FRONT_DOOR_ORIGIN=https://108.131.113.253`. The service is loopback-only; Nginx preserves the
  exact Host/Origin contract, limits requests, and never retries uncertain backend commands.
- The prepared unit leaves paid preparation disabled. Enable `--enable-preparation` only
  after an explicit OpenRouter API token is securely provisioned as `ANTHROPIC_AUTH_TOKEN`,
  with `ANTHROPIC_BASE_URL=https://openrouter.ai/api`. The host's existing Claude Max login is
  never a fallback. GitHub Actions secrets cannot be read back as a provisioning mechanism.
- The service HOME/XDG directories are separate from the abandoned supervisor login. Only
  `GH_CONFIG_DIR` points to existing owner authentication; that variable is removed from
  model subprocess environments. The source and the rest of the home directory are read-only.
- Resource limits bound this service and its child processes within the existing host.
  A killed or interrupted preparation leaves its durable reservation and is not retried.

## HTTPS sequence

Render the checked public IPv4 into `nginx.conf.template`. First serve only the HTTP challenge
server on port 80. Use Certbot's staging endpoint to verify the challenge path, without
presenting a staging certificate to users. Then obtain the production certificate with
`--cert-name dark-factory-frontdoor --preferred-profile shortlived --ip-address ADDRESS`
and webroot `/var/lib/dark-factory-frontdoor/acme`. Load the full HTTPS configuration only
after certificate files exist and `nginx -t` passes. Run the proxy under its own service;
do not silently replace an unrelated existing web server.

The renewal timer must run automatically and its deploy hook must validate configuration
before reloading the proxy. Verify a renewal dry run and an externally trusted TLS connection.
Verify unauthenticated private endpoints refuse, owner login succeeds, current scope/evidence
is real, and stop is only shown after remote confirmation. An IP change requires new verified
configuration and certificate issuance; do not allocate another paid resource to hide it.

These files are a reviewable payload, not a deployment receipt. The complete real product
execution cycle remains an activation prerequisite. No certificate, firewall rule, software
package or service has been installed by preparing them.

## Preparation evidence

Local Nginx 1.24 syntax validation passed with the configured certificate paths replaced by
throwaway certificates, unprivileged loopback ports and private temporary directories. The
Front Door API unit passed `systemd-analyze verify` after copying it with mode 0644. The proxy
and renewal units still need verification with installed runtime binaries on the target host.
The stock release package used for syntax checking was extracted locally and never installed
or served; deployment must use the target distribution's supported security update.

The separate public source release `7b351a3f883a50d0af914abf7ab66ce140856ea7` has been staged
on the named Lightsail host through a depth-one fetch from the existing public repository.
Its exact HEAD and clean status were checked. No current-release link, service, certificate,
credential transfer or firewall change accompanied staging. Source staging is not activation.
