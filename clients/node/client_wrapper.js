"use strict";

const path = require("path");

const slmp = require(path.resolve(__dirname, "../../../node-red-contrib-plc-comm-slmp/lib"));

function parseArgs(argv) {
  const args = {
    host: argv[0],
    port: Number(argv[1]),
    command: argv[2],
    address: argv[3] || "",
    extra: [],
    frame: "3e",
    series: "ql",
    mode: "word",
    target: "",
    wordDevs: "",
    dwordDevs: "",
    words: "",
    dwords: "",
    bits: "",
    wordBlocks: "",
    bitBlocks: "",
  };

  for (let index = 4; index < argv.length; index += 1) {
    const token = argv[index];
    if (token === "--frame") {
      args.frame = argv[++index];
      continue;
    }
    if (token === "--series") {
      args.series = argv[++index];
      continue;
    }
    if (token === "--mode") {
      args.mode = argv[++index];
      continue;
    }
    if (token === "--target") {
      args.target = argv[++index];
      continue;
    }
    if (token === "--word-devs") {
      args.wordDevs = argv[++index];
      continue;
    }
    if (token === "--dword-devs") {
      args.dwordDevs = argv[++index];
      continue;
    }
    if (token === "--words") {
      args.words = argv[++index];
      continue;
    }
    if (token === "--dwords") {
      args.dwords = argv[++index];
      continue;
    }
    if (token === "--bits") {
      args.bits = argv[++index];
      continue;
    }
    if (token === "--word-blocks") {
      args.wordBlocks = argv[++index];
      continue;
    }
    if (token === "--bit-blocks") {
      args.bitBlocks = argv[++index];
      continue;
    }
    args.extra.push(token);
  }

  return args;
}

function parseTarget(value) {
  if (!value) {
    return undefined;
  }
  const parts = String(value).split(",");
  return {
    network: Number.parseInt(parts[0], 10),
    station: Number.parseInt(parts[1], 10),
    moduleIO: Number.parseInt(parts[2], 10),
    multidrop: Number.parseInt(parts[3], 10),
  };
}

function parseNamedAddresses(text) {
  return String(text || "")
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
}

function isBitAddress(address) {
  if (address.includes(".")) {
    return true;
  }
  const device = slmp.parseDevice(address.split(":", 2)[0].trim());
  const info = slmp.DEVICE_CODES[device.code];
  return Boolean(info && info.unit === slmp.DeviceUnit.BIT);
}

function parseNamedScalar(address, rawValue) {
  const value = String(rawValue).trim();
  if (address.includes(".") || isBitAddress(address)) {
    return value === "1" || value.toLowerCase() === "true";
  }
  if (address.includes(":") && address.slice(address.lastIndexOf(":") + 1).trim().toUpperCase() === "F") {
    return Number.parseFloat(value);
  }
  return Number.parseInt(value, 0);
}

function parseNamedUpdates(text) {
  const updates = {};
  for (const item of parseNamedAddresses(text)) {
    const parts = item.split("=", 2);
    const address = parts[0].trim();
    updates[address] = parseNamedScalar(address, parts[1]);
  }
  return updates;
}

function parseKvPairs(text) {
  return String(text || "")
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean)
    .map((item) => {
      const parts = item.split("=", 2);
      return [parts[0].trim(), Number.parseInt(parts[1].trim(), 10)];
    });
}

function parseDevCountPairs(text) {
  return String(text || "")
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean)
    .map((item) => {
      const parts = item.split("=", 2);
      return [parts[0].trim(), Number.parseInt(parts[1].trim(), 10)];
    });
}

function parseDevValuesPairs(text) {
  return String(text || "")
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean)
    .map((item) => {
      const parts = item.split("=", 2);
      return [parts[0].trim(), parts[1].split(":").map((value) => Number.parseInt(value.trim(), 10))];
    });
}

function parseQualifiedDevice(text) {
  const token = String(text || "").trim().toUpperCase();
  let match = /^J(\d+)[\\/](.+)$/.exec(token);
  if (match) {
    return {
      device: slmp.parseDevice(match[2]),
      extensionSpecification: Number.parseInt(match[1], 10),
      directMemorySpecification: 0xf9,
    };
  }

  match = /^U([0-9A-F]+)[\\/](.+)$/.exec(token);
  if (!match) {
    return {
      device: slmp.parseDevice(token),
      extensionSpecification: null,
      directMemorySpecification: 0x00,
    };
  }

  const device = slmp.parseDevice(match[2]);
  let directMemorySpecification = 0x00;
  if (device.code === "G") {
    directMemorySpecification = 0xf8;
  } else if (device.code === "HG") {
    directMemorySpecification = 0xfa;
  }

  return {
    device,
    extensionSpecification: Number.parseInt(match[1], 16),
    directMemorySpecification,
  };
}

function numberToBuffer(value, size) {
  const buffer = Buffer.alloc(size);
  if (size === 2) {
    buffer.writeUInt16LE(Number(value) & 0xffff, 0);
    return buffer;
  }
  if (size === 4) {
    buffer.writeUInt32LE(Number(value) >>> 0, 0);
    return buffer;
  }
  throw new Error(`unsupported integer size: ${size}`);
}

function packBitValues(values) {
  const bytes = [];
  for (let index = 0; index < values.length; index += 2) {
    const high = values[index] ? 0x10 : 0x00;
    const low = index + 1 < values.length && values[index + 1] ? 0x01 : 0x00;
    bytes.push(high | low);
  }
  return Buffer.from(bytes);
}

function encodeExtendedDeviceSpec(deviceText, series) {
  const qualified = parseQualifiedDevice(deviceText);
  const info = slmp.DEVICE_CODES[qualified.device.code];
  if (!info) {
    throw new Error(`Unknown SLMP device code '${qualified.device.code}'`);
  }

  if (qualified.directMemorySpecification === 0xf9) {
    const buffer = Buffer.alloc(11);
    buffer.writeUIntLE(Number(qualified.device.number) & 0xffffff, 2, 3);
    buffer.writeUInt8(info.code & 0xff, 5);
    buffer.writeUInt8(Number(qualified.extensionSpecification) & 0xff, 8);
    buffer.writeUInt8(0xf9, 10);
    return buffer;
  }

  const extensionSpecification = Number(qualified.extensionSpecification || 0) & 0xffff;
  const deviceSpec = slmp.encodeDeviceSpec(qualified.device, { series });
  const dm = Number(qualified.directMemorySpecification || 0) & 0xff;
  const captureAligned =
    (qualified.device.code === "G" || qualified.device.code === "HG") && (dm === 0xf8 || dm === 0xfa);

  if (captureAligned) {
    return Buffer.concat([
      Buffer.from([0x00, 0x00]),
      deviceSpec,
      Buffer.from([0x00, 0x00]),
      numberToBuffer(extensionSpecification, 2),
      Buffer.from([dm]),
    ]);
  }

  return Buffer.concat([
    numberToBuffer(extensionSpecification, 2),
    Buffer.from([0x00, 0x00, 0x00]),
    deviceSpec,
    Buffer.from([dm]),
  ]);
}

function resolveExtendedSubcommand(deviceText, series, bitUnit) {
  const qualified = parseQualifiedDevice(deviceText);
  if (qualified.directMemorySpecification === 0xf9) {
    return bitUnit ? 0x0081 : 0x0080;
  }
  return slmp.resolveDeviceSubcommand({ bitUnit, series, extension: true });
}

function encodeDwordWords(value, mode) {
  const buffer = Buffer.alloc(4);
  if (mode === "float") {
    buffer.writeFloatLE(Number(value), 0);
  } else {
    buffer.writeUInt32LE(Number(value) >>> 0, 0);
  }
  return [buffer.readUInt16LE(0), buffer.readUInt16LE(2)];
}

function decodeDwordWords(words, offset, mode) {
  const buffer = Buffer.alloc(4);
  buffer.writeUInt16LE(Number(words[offset]) & 0xffff, 0);
  buffer.writeUInt16LE(Number(words[offset + 1]) & 0xffff, 2);
  if (mode === "float") {
    return buffer.readFloatLE(0);
  }
  return buffer.readUInt32LE(0);
}

function normalizeNamedValueForJson(address, value) {
  if (address.includes(".") || isBitAddress(address)) {
    return Boolean(value);
  }
  const dtype = address.includes(":") ? address.slice(address.lastIndexOf(":") + 1).trim().toUpperCase() : "U";
  if (dtype === "F") {
    return Number(value);
  }
  if (dtype === "D") {
    return Number(value) >>> 0;
  }
  if (dtype === "L") {
    return Number(value) | 0;
  }
  if (dtype === "S") {
    const buffer = Buffer.alloc(2);
    buffer.writeUInt16LE(Number(value) & 0xffff, 0);
    return buffer.readInt16LE(0);
  }
  return Number(value) & 0xffff;
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  const client = new slmp.SlmpClient({
    host: args.host,
    port: args.port,
    frameType: args.frame,
    plcSeries: args.series,
    target: parseTarget(args.target),
  });

  try {
    await client.connect();
    let result;

    if (args.command === "read") {
      const count = args.extra.length > 0 ? Number.parseInt(args.extra[0], 10) : 1;
      if (args.mode === "bit") {
        const values = await client.readDevices(args.address, count, { bitUnit: true });
        result = { status: "success", values: values.map((value) => (value ? 1 : 0)) };
      } else if (args.mode === "dword" || args.mode === "float") {
        const words = await client.readDevices(args.address, count * 2, { bitUnit: false });
        result = {
          status: "success",
          values: Array.from({ length: count }, (_, index) => decodeDwordWords(words, index * 2, args.mode)),
        };
      } else {
        result = { status: "success", values: await client.readDevices(args.address, count, { bitUnit: false }) };
      }
    } else if (args.command === "write") {
      if (args.mode === "bit") {
        await client.writeDevices(args.address, args.extra.map((value) => value === "1"), { bitUnit: true });
      } else if (args.mode === "dword" || args.mode === "float") {
        const words = [];
        for (const value of args.extra) {
          words.push(...encodeDwordWords(value, args.mode));
        }
        await client.writeDevices(args.address, words, { bitUnit: false });
      } else {
        await client.writeDevices(args.address, args.extra.map((value) => Number.parseInt(value, 10)), { bitUnit: false });
      }
      result = { status: "success" };
    } else if (args.command === "read-type") {
      const info = await client.readTypeName();
      result = {
        status: "success",
        model: info.model,
        model_code: info.modelCode == null ? null : `0x${info.modelCode.toString(16).toUpperCase()}`,
      };
    } else if (args.command === "random-read") {
      const wordDevices = args.wordDevs ? args.wordDevs.split(",").map((item) => item.trim()).filter(Boolean) : [];
      const dwordDevices = args.dwordDevs ? args.dwordDevs.split(",").map((item) => item.trim()).filter(Boolean) : [];
      const values = await client.readRandom({ wordDevices, dwordDevices });
      result = {
        status: "success",
        word_values: wordDevices.map((device) => values.word[device]),
        dword_values: dwordDevices.map((device) => values.dword[device]),
      };
    } else if (args.command === "random-write-words") {
      await client.writeRandomWords({
        wordValues: parseKvPairs(args.words),
        dwordValues: parseKvPairs(args.dwords),
      });
      result = { status: "success" };
    } else if (args.command === "random-write-bits") {
      await client.writeRandomBits({
        bitValues: parseKvPairs(args.bits).map(([device, value]) => [device, Boolean(value)]),
      });
      result = { status: "success" };
    } else if (args.command === "monitor-register") {
      const wordDevices = args.wordDevs ? args.wordDevs.split(",").map((item) => item.trim()).filter(Boolean) : [];
      const dwordDevices = args.dwordDevs ? args.dwordDevs.split(",").map((item) => item.trim()).filter(Boolean) : [];
      const parts = [Buffer.from([wordDevices.length, dwordDevices.length])];
      wordDevices.forEach((device) => parts.push(slmp.encodeDeviceSpec(device, { series: args.series })));
      dwordDevices.forEach((device) => parts.push(slmp.encodeDeviceSpec(device, { series: args.series })));
      const subcommand = args.series === "iqr" ? 0x0002 : 0x0000;
      await client.request(slmp.Command.MONITOR_REGISTER, subcommand, Buffer.concat(parts), { series: args.series });
      result = { status: "success" };
    } else if (args.command === "block-read") {
      const wordBlocks = parseDevCountPairs(args.wordBlocks);
      const bitBlocks = parseDevCountPairs(args.bitBlocks);
      const values = await client.readBlock({ wordBlocks, bitBlocks });
      result = {
        status: "success",
        word_values: values.wordValues,
        bit_values: values.bitWordValues,
        word_blocks: values.wordBlocks.map((block) => [block.device, block.values]),
        bit_blocks: values.bitBlocks.map((block) => [block.device, block.values]),
      };
    } else if (args.command === "block-write") {
      await client.writeBlock({
        wordBlocks: parseDevValuesPairs(args.wordBlocks),
        bitBlocks: parseDevValuesPairs(args.bitBlocks),
      });
      result = { status: "success" };
    } else if (args.command === "read-named" || args.command === "poll-once") {
      const addresses = parseNamedAddresses(args.address);
      const values = await slmp.readNamed(client, addresses);
      result = {
        status: "success",
        addresses,
        values: addresses.map((address) => normalizeNamedValueForJson(address, values[address])),
      };
    } else if (args.command === "write-named") {
      await slmp.writeNamed(client, parseNamedUpdates(args.address));
      result = { status: "success" };
    } else if (args.command === "read-ext") {
      const count = args.extra.length > 0 ? Number.parseInt(args.extra[0], 10) : 1;
      const bitUnit = args.mode === "bit";
      const payload = Buffer.concat([
        encodeExtendedDeviceSpec(args.address, args.series),
        numberToBuffer(count, 2),
      ]);
      const response = await client.request(
        slmp.Command.DEVICE_READ,
        resolveExtendedSubcommand(args.address, args.series, bitUnit),
        payload,
        { series: args.series }
      );
      if (bitUnit) {
        result = {
          status: "success",
          values: slmp.unpackBitValues(response.data, count).map((value) => (value ? 1 : 0)),
        };
      } else {
        const words = slmp.decodeDeviceWords(response.data);
        result = { status: "success", values: words };
      }
    } else if (args.command === "write-ext") {
      const bitUnit = args.mode === "bit";
      const payloadParts = [
        encodeExtendedDeviceSpec(args.address, args.series),
        numberToBuffer(args.extra.length, 2),
      ];
      if (bitUnit) {
        payloadParts.push(packBitValues(args.extra.map((value) => value === "1")));
      } else {
        args.extra.forEach((value) => payloadParts.push(numberToBuffer(Number.parseInt(value, 10), 2)));
      }
      await client.request(
        slmp.Command.DEVICE_WRITE,
        resolveExtendedSubcommand(args.address, args.series, bitUnit),
        Buffer.concat(payloadParts),
        { series: args.series }
      );
      result = { status: "success" };
    } else {
      result = { status: "error", message: `unsupported command: ${args.command}` };
    }

    process.stdout.write(`${JSON.stringify(result)}\n`);
  } catch (error) {
    process.stdout.write(`${JSON.stringify({ status: "error", message: error.message || String(error) })}\n`);
  } finally {
    await client.close().catch(() => undefined);
  }
}

main();
