# memprobe-action

Firmware size reports on every pull request, powered by [memprobe](https://memprobe.dev). Posts a PR comment with flash/RAM totals and symbol-level changes, and fails the job when a memory budget is exceeded.

The ELF is parsed on the runner and only section and symbol metadata is sent to the API, the same information `readelf -S` and `nm` print.

## Quick start

```yaml
name: firmware-size
on: [pull_request]

permissions:
  pull-requests: write

jobs:
  size:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Build firmware
        run: make   # produces build/firmware.elf
      - uses: memprobe-dev/memprobe-action@v1
        with:
          file: build/firmware.elf
          api-key: ${{ secrets.MEMPROBE_API_KEY }}
```

Create the API key in [Account settings](https://memprobe.dev/account) and add it to the repository as the `MEMPROBE_API_KEY` secret. The `pull-requests: write` permission is needed to post the comment.

## Budgets

Add a `memprobe.toml` next to your build (run `memprobe init` to scaffold one):

```toml
[budgets]
flash = "512KB"
ram   = "128KB"

# Physical part capacity. Adds utilization percentages to the report.
[regions]
flash = "1MB"
ram   = "320KB"
```

The action picks it up automatically and fails the job when a budget is exceeded. You can also pass `budget-flash` / `budget-ram` inputs instead of the file.

## Size diff

Set `project` and the report compares each run against the project's baseline build stored at memprobe.dev, so there is nothing extra to build:

```yaml
      - uses: memprobe-dev/memprobe-action@v1
        with:
          file: build/firmware.elf
          project: my-firmware
          fail-on: 'flash:+2KB'
          api-key: ${{ secrets.MEMPROBE_API_KEY }}
```

By default the baseline is the newest saved build; pin a specific build as the baseline in your project at memprobe.dev to compare against a fixed reference instead. The first run has nothing to compare against and skips the diff.

To compare two local files instead, build the base branch and pass it as `base-file`:

```yaml
      - name: Build base
        run: |
          git fetch origin ${{ github.base_ref }}
          git checkout origin/${{ github.base_ref }}
          make
          cp build/firmware.elf /tmp/base.elf
          git checkout ${{ github.sha }}
      - name: Build PR
        run: make
      - uses: memprobe-dev/memprobe-action@v1
        with:
          file: build/firmware.elf
          base-file: /tmp/base.elf
          fail-on: 'flash:+2KB'
          api-key: ${{ secrets.MEMPROBE_API_KEY }}
```

The PR comment then includes the flash/RAM delta and the largest symbol changes. `fail-on` fails the job when growth passes the limit.

## Inputs

| Input | Required | Default | Meaning |
|---|---|---|---|
| `file` | yes | | Path to the firmware ELF. |
| `api-key` | yes | | memprobe API key. Use a repository secret. |
| `base-file` | no | | ELF to diff against. Unneeded when `project` is set. |
| `project` | no | | Project name at memprobe.dev. Saves the build there and diffs against the project baseline. |
| `fail-on` | no | | Fail when growth passes a limit, like `flash:+2KB` or `ram:0`. Needs `base-file` or `project`. |
| `budget-flash` | no | | Max flash, like `512KB`. Overrides `memprobe.toml`. |
| `budget-ram` | no | | Max RAM, like `128KB`. Overrides `memprobe.toml`. |
| `comment` | no | `true` | Post or update the PR comment. |
| `cli-version` | no | latest | memprobe package version to install. |
| `github-token` | no | `github.token` | Token used to post the PR comment. |

## Outputs

| Output | Meaning |
|---|---|
| `flash` | Flash usage in bytes. |
| `ram` | RAM usage in bytes. |
| `flash-delta` | Flash change in bytes versus `base-file`. Empty without a base. |
| `ram-delta` | RAM change in bytes versus `base-file`. Empty without a base. |
| `passed` | `true` when no budget or `fail-on` limit was breached. |

## Notes

- Every run saves a build to your memprobe history, so trends show up at [memprobe.dev](https://memprobe.dev).
- On `push` events the report goes to the job summary instead of a comment.
- Works with any toolchain that produces an ELF: GCC, Clang, IAR, Keil, ESP-IDF, Zephyr, PlatformIO.

## License

MIT
