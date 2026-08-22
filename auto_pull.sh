#!/bin/bash
set -e
cd "$(dirname "$0")"

# Configure local git to avoid auto-fetching unnecessary tags
git config fetch.unpackLimit 1

# Pull fast-forward changes directly in a single network pass
git pull --ff-only --no-tags --depth=1 origin main

