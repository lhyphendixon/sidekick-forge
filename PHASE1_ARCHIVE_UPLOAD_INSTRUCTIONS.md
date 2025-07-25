# Phase 1 Archive Upload Instructions

## Archive Details
- **File**: `legacy_agent_runtime_archive.tar.gz`
- **Size**: 273KB
- **Location**: `/tmp/legacy_agent_runtime_archive.tar.gz`
- **Created**: January 24, 2025

## External Storage Location
- **Platform**: Dropbox Shared Folder
- **URL**: https://www.dropbox.com/scl/fo/jboloph79z1vq3t87llw1/AGAzkKmNxKTj2145VTlVIQw?rlkey=1myl9x1ds7xhdpkqp7bbekcxf&st=436expyh&dl=0

## Manual Upload Instructions
Since programmatic upload requires Dropbox API credentials, please manually upload the archive:

1. Download the archive from the server:
   ```bash
   scp root@<server-ip>:/tmp/legacy_agent_runtime_archive.tar.gz ./
   ```

2. Open the Dropbox shared folder link in your browser

3. Upload `legacy_agent_runtime_archive.tar.gz` to the folder

4. Once uploaded, delete the local temp file:
   ```bash
   rm /tmp/legacy_agent_runtime_archive.tar.gz
   ```

## Verification
After upload, confirm the file is accessible in the Dropbox folder and update this document with the confirmation.

## Important Note
This archive contains the complete `/opt/autonomite-saas/agent-runtime/` directory as it existed on January 24, 2025, before the agent codebase unification project began.