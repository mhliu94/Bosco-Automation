# Source attribution

The Kafka runner, execution journal/workflows, price-book reader, KTrader order-status publisher, polling state, diagnostics, and associated regression tests were adapted from the user's [mhliu94/GigaMoney-Automation-Linux](https://github.com/mhliu94/GigaMoney-Automation-Linux), revision `07a0416e98f86b9896b805e3f1b75fd3947c0ed6`.

The BSL native protocol client, adapter, session enrollment, native mapping, calendar integration, CLI packaging, and native tests were developed for this project. No APK, decompiled application source, private broker traffic, or account credentials are distributed in the source archive. This notice records provenance and does not add a license grant to the reference project's code.

Runtime dependencies are separately licensed by their authors: cryptography, confluent-kafka/librdkafka, exchange_calendars, pandas, portalocker, and their dependencies. See their installed package metadata. Optional Frida tools are needed only for enrollment.
