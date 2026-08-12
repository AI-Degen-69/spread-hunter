#!/bin/bash

# Vly Git Sync Project Setup Script
# Project: Alpine Trade View
# This script will set up your local development environment

set -e  # Exit on error

echo '🚀 Starting Vly project setup...'

# Check Node.js installation
if ! command -v node &> /dev/null; then
    echo '❌ Error: Node.js is not installed.'
    echo 'Please install Node.js from https://nodejs.org/'
    exit 1
fi
echo '✅ Node.js is installed'

# Check for bun installation
if ! command -v bun &> /dev/null; then
    echo '❌ Error: Bun is not installed.'
    echo 'Please install Bun from https://bun.sh/'
    exit 1
fi
echo '✅ Bun is installed'

# Install dependencies
echo 'Installing dependencies...'
bun install --force
echo '✅ Dependencies installed'

# Setup Convex
echo 'Setting up Convex...'
if [ ! -f .env.local ]; then
    echo '⚠️  Warning: .env.local file not found.'
    echo 'Please download it from the Vly dashboard and place it in the project root.'
fi

# Initialize Convex (if not already initialized)
if [ ! -d 'convex/_generated' ]; then
    echo 'Initializing Convex...'
    bunx convex dev --once
fi
echo '✅ Convex is set up'

# Set up backend environment variables
if [ -f setup-backend-env.sh ]; then
    echo 'Setting up backend environment variables...'
    chmod +x setup-backend-env.sh
    ./setup-backend-env.sh
else
    echo '⚠️  Warning: setup-backend-env.sh not found.'
    echo 'Please download it from the Vly dashboard to set up backend environment variables.'
fi

echo ''
echo '🎉 Setup complete!'
echo ''
echo 'To start the development server, run:'
echo '  pnpm dev'
echo ''
echo 'Or run frontend and backend separately:'
echo '  pnpm dev:frontend  # In one terminal'
echo '  pnpm dev:backend   # In another terminal'
