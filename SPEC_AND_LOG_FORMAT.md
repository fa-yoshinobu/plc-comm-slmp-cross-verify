# SLMP Verification Detailed Specifications and Log Format

## Mock Server Specifications (`server/mock_server.py`)

### Supported Subcommands
- `0000 / 0001`: Q/L Word / Bit
- `0002 / 0003`: iQ-R Word / Bit
- `0080 / 0081`: Extended Device Q/L Word / Bit (Extended Address)
- `0082 / 0083`: Extended Device iQ-R Word / Bit (Extended Address)

### Simulated Error Scenarios (NG Patterns)
SLMP errors are intentionally returned under the following conditions:
- **Address out of range (`0xC050`)**: When `999999` is specified as the starting address.
- **Data length too large (`0xC056`)**: When `1001` or more points are specified for reading.
- **Invalid device code (`0xC051`)**: When an invalid device code (internally `0xEE`, etc.) is sent.

## Log File Format (`logs/*.json`)

Verification results are saved in the following structure. They can be used for patch writing to actual devices or for analysis.

```json
{
  "test_name": "4E Bit M Write",      // Test case name
  "frame_type": "4e",                 // 3e or 4e
  "command": "write",                 // read / write / read-type / remote-run / remote-stop
  "address": "M16",                   // Specified address
  "values": [1, 0, 1, 0, "--mode", "bit"], // Input arguments
  "request": "54000000000000ffff03000e001000011401001000009004001010", // Raw request packet (Hex)
  "response": "d4000000000000ffff030002000000" // Raw response packet from server (Hex)
}
```

## Packet Retransmission Tool Specifications (`slmp_interactive_sender.py`)

- Automatically loads `logs/latest.json`.
- **Header Analysis Function**: Automatically distinguishes response packet lengths (3E vs 4E) to receive responses of accurate length.
- **Timeout**: Set to 3.0 seconds.

