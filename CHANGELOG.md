# Changelog
All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](http://keepachangelog.com/en/1.0.0/)
and this project adheres to [Semantic Versioning](http://semver.org/spec/v2.0.0.html).

## [Unreleased]
### Fixed
- Unclear error message when referring to undefined container in `link` clause

### Added
- `dockwrkr login` command for logging into all defined registries
- Jobs feature. Define short-lived `jobs` in `dockwrkr.yml` which are executed
  in an ad-hoc manner.

### Changed
- `dockwrkr.yml`: `containers` has been renamed to `services`. Support for the
  `containers` key is maintained until next major release.

## [1.0.1] - 2017-06-13

### Added
- Networking guide to `README.md`

### Fixed
- Broken network detection with Docker 1.11

## [1.0.0] - 2017-06-13

Initial release.

