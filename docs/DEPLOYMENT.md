# Auto-updating deployment (Forgejo + Komodo)

Powarr is built into an image by Komodo, stored in the Forgejo container registry, and
redeployed by Komodo when a new image appears. No CI runner is needed.

```
merge to master -> Forgejo webhook -> Komodo Build "powarr"
   -> git.pwrs.dev/powers/powarr:latest (+ version tag)
   -> Komodo Stack "powarr" sees the new digest -> redeploys
```

Komodo is the only updater. Do not also run Watchtower on this container (the compose
file sets `com.centurylinklabs.watchtower.enable=false`).

## One-time setup (manual)

1. **Forgejo token.** Create an access token for the `powers` user with package
   read/write and repository read scopes only.
2. **Komodo accounts.** Settings > Providers: add a Git provider (`git.pwrs.dev`, https)
   and a Docker registry (`git.pwrs.dev`) using that token.
3. **Variable.** Settings > Variables: add `POWARR_DB_PASSWORD` as a secret, using the
   current Postgres password (rotate it first if the old one was ever in git history).
4. **Resources.** Create the Build and Stack from `deploy/komodo/resources.toml`
   (paste into a Resource Sync, or create them in the UI with the same values).
   The config path in `deploy/komodo/compose.yaml` is `/mnt/ServerFiles/Docker/powarr`; change it if yours differs.
5. **Webhook.** In Forgejo repo Settings > Webhooks, add a push webhook for `master`
   pointing at the Build's webhook URL shown in Komodo (Build > Config > Webhook).
6. **First run.** Run the Build once, then Deploy the Stack. Stop the old
   `build: .` container first so port 7979 and the name `powarr` are free. The
   `/config` volume and Postgres are untouched.

## Day to day

Merge to `master`. The image builds, the Stack redeploys. Roll back by redeploying the
Stack with a version tag instead of `latest`.
