===========================================
Soupglasses Augeas Collection Release Notes
===========================================

.. contents:: Topics

v1.0.0
======

Release Summary
---------------

Initial stable release.

Minor Changes
-------------

- Add copy-style application validation before replacing the managed file.
- Add declarative merged, replaced, deleted, and gathered resource states.
- Add executable before, task, and after examples for 109 stock-lens cases.
- Add standard Ansible file attributes alongside Augeas configuration state.
- Add tested macOS and FreeBSD 15 support, including both python-augeas CFFI layouts.

New Modules
-----------

- soupglasses.augeas.augeas - Manage configuration through Augeas resource semantics.
