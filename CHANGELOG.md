# Changelog

## [0.1.1](https://github.com/joshorr/dynamo-pydantic/compare/v0.1.0...v0.1.1) (2026-07-12)


### Bug Fixes

* pdoc generator, for API reference doc generation. ([755ea17](https://github.com/joshorr/dynamo-pydantic/commit/755ea17645374630f6e483fc66f332b7000077c0))

## 0.1.0 (2026-07-12)


### Features

* About 1/2 way though converting old library to use Pydantic. ([2b148aa](https://github.com/joshorr/dynamo-pydantic/commit/2b148aa7c7f5cae751d9f4ab98745245c19ce2e6))
* add easy option/way to make datetime fields always use utc; for more reliable dynamodb query behavior. ([342730f](https://github.com/joshorr/dynamo-pydantic/commit/342730f5fd732d58df3bf0406be7373b06587c6c))
* add tests and finish support for multiple hash/range fields. ([42431c4](https://github.com/joshorr/dynamo-pydantic/commit/42431c4a80cf29ea5c3fe8c5f7ca96ef99678c55))
* get basic 'get' working with plain dict values; will do pydantic models next. ([5be0e79](https://github.com/joshorr/dynamo-pydantic/commit/5be0e798ed8794c4be6965eaf4988b83d9ebbde1))
* Renamed a few attrs/methods + added a bunch of basic docs. ([e288acc](https://github.com/joshorr/dynamo-pydantic/commit/e288acc207490f8004a111ec3526766fbf8f94c3))
* support for more advanced gets, and subclassing client and attaching it to a dyn-model subclass. ([f767a8f](https://github.com/joshorr/dynamo-pydantic/commit/f767a8f248ca386ff664c7482832b1de7be4b30b))


### Bug Fixes

* many bugs found and fix from old unit tests + adapted old unit tests to new api interface. ([30f7197](https://github.com/joshorr/dynamo-pydantic/commit/30f719719440e85a28ce5112056105cee57091b1))
