#!/bin/bash
# RAM disk setup for #903 ablation RAM arm.
# Source this or run commands manually. Record attach/detach receipts.
set -euo pipefail

ACTION="${1:-attach}"
RECEIPT_DIR="${2:-experiments/baseline-903/raw/ablation}"

if [ "$ACTION" = "attach" ]; then
    echo "Creating 2GB RAM disk..."
    RAMDISK_DEV=$(hdiutil attach -nomount ram://4194304)
    RAMDISK_DEV=$(echo "$RAMDISK_DEV" | xargs)  # trim whitespace
    echo "Device: $RAMDISK_DEV"
    diskutil erasevolume HFS+ "RamDisk903" "$RAMDISK_DEV"
    echo "Mounted at /Volumes/RamDisk903"
    
    # Record attach receipt
    cat > "$RECEIPT_DIR/ramdisk-attach.json" << EOF
{
  "action": "attach",
  "device": "$RAMDISK_DEV",
  "mount": "/Volumes/RamDisk903",
  "timestamp": "$(date -u '+%Y-%m-%dT%H:%M:%SZ')",
  "sectors": 4194304,
  "bytes": 2147483648
}
EOF
    echo "Attach receipt written to $RECEIPT_DIR/ramdisk-attach.json"
    echo "$RAMDISK_DEV"  # last line is device for capture

elif [ "$ACTION" = "detach" ]; then
    DEVICE="${3:?Usage: $0 detach RECEIPT_DIR DEVICE}"
    echo "Detaching $DEVICE..."
    hdiutil detach "$DEVICE" || hdiutil detach "$DEVICE" -force
    
    cat > "$RECEIPT_DIR/ramdisk-detach.json" << EOF
{
  "action": "detach",
  "device": "$DEVICE",
  "timestamp": "$(date -u '+%Y-%m-%dT%H:%M:%SZ')",
  "forced": false
}
EOF
    echo "Detach receipt written to $RECEIPT_DIR/ramdisk-detach.json"
fi
