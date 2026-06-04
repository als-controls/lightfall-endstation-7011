# lightfall-endstation-7011

Lightfall plugins for the ALS Beamline 7.0.1.1 endstation.

This package extends [Lightfall](https://github.com/als-controls/lightfall) with
beamline-specific functionality, registered via the `lightfall.plugins` entry point:

- **Blackfly observer** — pure-Python GigE Vision client for FLIR Blackfly S
  cameras, with a `bfly-discover` CLI for finding cameras on the network
- **Variance pipeline** — notebook-based variance computation
  (`lightfall_pipelines.pipeline` entry point)

## Installation

```bash
pip install -e .
```

## Development

```bash
pip install -e ".[dev]"
pytest
```

Hardware tests against a live Blackfly S camera are marked `hw` and require the
`BLACKFLY_TEST_IP` environment variable:

```bash
BLACKFLY_TEST_IP=192.168.x.x pytest -m hw
```

## License

See [LICENSE.md](LICENSE.md) and [LEGAL.md](LEGAL.md).
