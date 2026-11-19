# Contributing

Bug reports, ideas and code contributions are welcome.

- **Bugs and feature requests:** pick a template on the [Issues](../../issues) page. Use Discussions for questions and ideas.
- **Security vulnerabilities:** see [SECURITY.md](SECURITY.md).

## Code contributions

1. Fork the repository and create a branch.
2. Add a test for your change and run the tests:
   ```sh
   cd open-kobra && ./test.sh
   cd kobra-screen && python3 -m unittest discover -p 'test_*.py'
   cd ace-driver && python3 -m unittest discover -s tests
   cd leviq3-klipper && python3 -m unittest test_leviq3
   ```
3. Shell scripts run under BusyBox `sh` on the printer: no bash-only syntax (`test.sh` checks this).
4. The printer runs Python 3.11 (uClibc); do not add dependencies outside the standard library.
5. Open a pull request describing what changed and whether you tried it on a printer.

Contributions are accepted under GPLv3.
