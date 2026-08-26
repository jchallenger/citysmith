"""Static assets for :mod:`citysmith.uiserver`.

A package rather than a bare directory so that ``setuptools``' package finder
picks it up and an installed citysmith still has a page to serve. There is no
code here: ``index.html``, ``app.css`` and ``app.js`` are read off disk by the
server, which is also what makes editing them a reload rather than a restart.
"""
