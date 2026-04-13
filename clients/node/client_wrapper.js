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
