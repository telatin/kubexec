#!/bin/bash
echo "This is a test script!"
echo "Current directory: $(pwd)"
echo "Available files in /shared:"
ls -la /shared/
echo "Environment variables:"
env | grep -E "^(HOME|USER|PATH)" | head -3
echo "Script completed successfully!"