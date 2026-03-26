#include <iostream>
#include <string>
#include <vector>
#include <sstream>
#include <iomanip>
#include <chrono>
#include <cstring>

#ifdef _WIN32
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <winsock2.h>
#include <ws2tcpip.h>
#else
#include <fcntl.h>
#include <netdb.h>
#include <sys/ioctl.h>
#include <sys/select.h>
#include <sys/socket.h>
#include <unistd.h>
#endif

#include "slmp_minimal.h"

// --- Socket Transport ---
#ifdef _WIN32
using SocketHandle = SOCKET;
constexpr SocketHandle kInvalidSocket = INVALID_SOCKET;
struct SocketRuntimeInit { SocketRuntimeInit() { WSADATA d = {}; WSAStartup(MAKEWORD(2, 2), &d); } ~SocketRuntimeInit() { WSACleanup(); } };
void closeSocket(SocketHandle h) { if (h != kInvalidSocket) closesocket(h); }
#else
using SocketHandle = int;
constexpr SocketHandle kInvalidSocket = -1;
void closeSocket(SocketHandle h) { if (h != kInvalidSocket) close(h); }
#endif

class SocketTransport : public slmp::ITransport {
public:
    SocketTransport() : socket_(kInvalidSocket), connected_(false) {}
    ~SocketTransport() override { close(); }
    bool connect(const char* host, uint16_t port) override {
        close();
#ifdef _WIN32
        static SocketRuntimeInit r;
#endif
        char pt[8]; std::snprintf(pt, sizeof(pt), "%u", port);
        addrinfo hints = {}, *res = nullptr; hints.ai_family = AF_UNSPEC; hints.ai_socktype = SOCK_STREAM; hints.ai_protocol = IPPROTO_TCP;
        if (getaddrinfo(host, pt, &hints, &res) != 0) return false;
        for (addrinfo* it = res; it != nullptr; it = it->ai_next) {
            SocketHandle h = socket(it->ai_family, it->ai_socktype, it->ai_protocol);
            if (h == kInvalidSocket) continue;
            if (::connect(h, it->ai_addr, static_cast<int>(it->ai_addrlen)) == 0) {
                socket_ = h; connected_ = true;
#ifdef _WIN32
                u_long m = 1; ioctlsocket(socket_, FIONBIO, &m);
#else
                fcntl(socket_, F_SETFL, fcntl(socket_, F_GETFL, 0) | O_NONBLOCK);
#endif
                freeaddrinfo(res); return true;
            }
            closeSocket(h);
        }
        freeaddrinfo(res); return false;
    }
    void close() override { closeSocket(socket_); socket_ = kInvalidSocket; connected_ = false; }
    bool connected() const override { return connected_; }
    bool writeAll(const uint8_t* data, size_t len) override {
        if (!connected_ || data == nullptr) return false;
        size_t off = 0;
        while (off < len) {
            int s = ::send(socket_, (const char*)data + off, (int)(len - off), 0);
            if (s <= 0) { close(); return false; }
            off += (size_t)s;
        }
        return true;
    }
    bool readExact(uint8_t* data, size_t len, uint32_t ms) override {
        if (!connected_ || data == nullptr) return false;
        auto dl = std::chrono::steady_clock::now() + std::chrono::milliseconds(ms);
        size_t off = 0;
        while (off < len) {
            auto now = std::chrono::steady_clock::now();
            if (now >= dl) return false;
            if (!waitReadable((uint32_t)std::chrono::duration_cast<std::chrono::milliseconds>(dl - now).count())) return false;
            int r = ::recv(socket_, (char*)data + off, (int)(len - off), 0);
            if (r <= 0) {
#ifdef _WIN32
                if (WSAGetLastError() == WSAEWOULDBLOCK) continue;
#else
                if (errno == EWOULDBLOCK || errno == EAGAIN) continue;
#endif
                close(); return false;
            }
            off += (size_t)r;
        }
        return true;
    }
    size_t write(const uint8_t* d, size_t l) override { return writeAll(d, l) ? l : 0; }
    size_t read(uint8_t* d, size_t l) override { if (!connected_) return 0; int r = ::recv(socket_, (char*)d, (int)l, 0); return r > 0 ? (size_t)r : 0; }
    size_t available() override { if (!connected_) return 0; u_long a = 0; ioctlsocket(socket_, FIONREAD, &a); return (size_t)a; }
private:
    bool waitReadable(uint32_t ms) const {
        fd_set rs; FD_ZERO(&rs); FD_SET(socket_, &rs);
        timeval t = {(long)(ms/1000), (long)((ms%1000)*1000)};
        return select(0, &rs, nullptr, nullptr, &t) > 0;
    }
    SocketHandle socket_ = kInvalidSocket; bool connected_ = false;
};

// --- Helpers ---
static int parseAutoInt(const std::string& s) {
    if (s.size() > 2 && s[0] == '0' && (s[1] == 'x' || s[1] == 'X'))
        return (int)std::stoul(s, nullptr, 16);
    return std::stoi(s);
}

static std::vector<std::string> splitStr(const std::string& s, char delim) {
    std::vector<std::string> out;
    std::string cur;
    for (char c : s) { if (c == delim) { if (!cur.empty()) out.push_back(cur); cur.clear(); } else cur += c; }
    if (!cur.empty()) out.push_back(cur);
    return out;
}

// Key=Value list: "D100=100,D101=200"
static std::vector<std::pair<std::string,int>> parseKvPairs(const std::string& s) {
    std::vector<std::pair<std::string,int>> out;
    if (s.empty()) return out;
    for (auto& item : splitStr(s, ',')) {
        auto eq = item.find('=');
        if (eq == std::string::npos) continue;
        out.push_back({item.substr(0, eq), std::stoi(item.substr(eq+1))});
    }
    return out;
}

// "D100=3,D200=2" -> pairs of (device_str, count)
static std::vector<std::pair<std::string,int>> parseDevCountPairs(const std::string& s) {
    return parseKvPairs(s);
}

// "D100=10:20:30" -> pairs of (device_str, [values])
static std::vector<std::pair<std::string,std::vector<int>>> parseDevValuesPairs(const std::string& s) {
    std::vector<std::pair<std::string,std::vector<int>>> out;
    if (s.empty()) return out;
    for (auto& item : splitStr(s, ',')) {
        auto eq = item.find('=');
        if (eq == std::string::npos) continue;
        std::string dev = item.substr(0, eq);
        std::vector<int> vals;
        for (auto& v : splitStr(item.substr(eq+1), ':')) vals.push_back(std::stoi(v));
        out.push_back({dev, vals});
    }
    return out;
}

static slmp::DeviceAddress parseDevice(const std::string& addr) {
    if (addr.empty()) return {slmp::DeviceCode::D, 0};
    if (addr.find('\\') != std::string::npos) throw std::runtime_error("use ext command for qualified devices");
    size_t ep = 0; while (ep < addr.length() && isalpha((unsigned char)addr[ep])) ep++;
    std::string px = addr.substr(0, ep);
    std::string pxU = px;
    for (auto& c : pxU) c = (char)toupper((unsigned char)c);
    // Hex-addressed devices
    bool isHex = (pxU=="X"||pxU=="Y"||pxU=="B"||pxU=="W"||pxU=="SB"||pxU=="SW"||pxU=="DX"||pxU=="DY");
    uint32_t nm = (uint32_t)std::stoul(addr.substr(ep), nullptr, isHex ? 16 : 10);
    // Word devices (decimal)
    if (pxU=="D")    return slmp::dev::D(slmp::dev::dec(nm));
    if (pxU=="M")    return slmp::dev::M(slmp::dev::dec(nm));
    if (pxU=="L")    return slmp::dev::L(slmp::dev::dec(nm));
    if (pxU=="V")    return slmp::dev::V(slmp::dev::dec(nm));
    if (pxU=="SM")   return slmp::dev::SM(slmp::dev::dec(nm));
    if (pxU=="SD")   return slmp::dev::SD(slmp::dev::dec(nm));
    if (pxU=="R")    return slmp::dev::R(slmp::dev::dec(nm));
    if (pxU=="ZR")   return slmp::dev::ZR(slmp::dev::dec(nm));
    if (pxU=="Z")    return {slmp::DeviceCode::Z,  nm};
    if (pxU=="LZ")   return {slmp::DeviceCode::LZ, nm};
    // Hex-addressed devices
    if (pxU=="X")    return slmp::dev::X(slmp::dev::hex(nm));
    if (pxU=="Y")    return slmp::dev::Y(slmp::dev::hex(nm));
    if (pxU=="B")    return slmp::dev::B(slmp::dev::hex(nm));
    if (pxU=="W")    return slmp::dev::W(slmp::dev::hex(nm));
    if (pxU=="SB")   return slmp::dev::SB(slmp::dev::hex(nm));
    if (pxU=="SW")   return slmp::dev::SW(slmp::dev::hex(nm));
    if (pxU=="DX")   return slmp::dev::DX(slmp::dev::hex(nm));
    if (pxU=="DY")   return slmp::dev::DY(slmp::dev::hex(nm));
    // Annunciator (F conflicts with C macro, uses named helper)
    if (pxU=="F")    return slmp::dev::FDevice(slmp::dev::dec(nm));
    // Timer
    if (pxU=="TN")   return slmp::dev::TN(slmp::dev::dec(nm));
    if (pxU=="TS")   return slmp::dev::TS(slmp::dev::dec(nm));
    if (pxU=="TC")   return slmp::dev::TC(slmp::dev::dec(nm));
    // Long Timer
    if (pxU=="LTN")  return {slmp::DeviceCode::LTN, nm};
    if (pxU=="LTS")  return {slmp::DeviceCode::LTS, nm};
    if (pxU=="LTC")  return {slmp::DeviceCode::LTC, nm};
    // Retentive Timer
    if (pxU=="STN")  return slmp::dev::STN(slmp::dev::dec(nm));
    if (pxU=="STS")  return slmp::dev::STS(slmp::dev::dec(nm));
    if (pxU=="STC")  return slmp::dev::STC(slmp::dev::dec(nm));
    // Long Retentive Timer
    if (pxU=="LSTN") return {slmp::DeviceCode::LSTN, nm};
    if (pxU=="LSTS") return {slmp::DeviceCode::LSTS, nm};
    if (pxU=="LSTC") return {slmp::DeviceCode::LSTC, nm};
    // Counter
    if (pxU=="CN")   return slmp::dev::CN(slmp::dev::dec(nm));
    if (pxU=="CS")   return slmp::dev::CS(slmp::dev::dec(nm));
    if (pxU=="CC")   return slmp::dev::CC(slmp::dev::dec(nm));
    // Long Counter
    if (pxU=="LCN")  return slmp::dev::LCN(slmp::dev::dec(nm));
    if (pxU=="LCS")  return slmp::dev::LCS(slmp::dev::dec(nm));
    if (pxU=="LCC")  return slmp::dev::LCC(slmp::dev::dec(nm));
    throw std::runtime_error("unknown device code: " + pxU);
}

// Parse "J1\SW0" or "U3\G100" style qualified device
struct QualifiedAddr { bool is_link_direct; uint8_t j_net; uint16_t slot; bool use_hg; slmp::DeviceCode code; uint32_t dev_no; };
static QualifiedAddr parseQualifiedDevice(const std::string& addr) {
    QualifiedAddr q{};
    auto sep = addr.find('\\');
    if (sep == std::string::npos) sep = addr.find('/');
    if (sep == std::string::npos) throw std::runtime_error("not a qualified device: " + addr);
    std::string prefix = addr.substr(0, sep);
    std::string device_part = addr.substr(sep + 1);
    if (!prefix.empty() && (prefix[0]=='J'||prefix[0]=='j')) {
        q.is_link_direct = true;
        q.j_net = (uint8_t)std::stoul(prefix.substr(1));
        auto dev = parseDevice(device_part);
        q.code = dev.code;
        q.dev_no = dev.number;
    } else if (!prefix.empty() && (prefix[0]=='U'||prefix[0]=='u')) {
        q.is_link_direct = false;
        q.slot = (uint16_t)std::stoul(prefix.substr(1), nullptr, 16);
        std::string devU = device_part;
        for (auto& c : devU) c = (char)toupper((unsigned char)c);
        if (devU.size() > 2 && devU.substr(0,2) == "HG") {
            q.use_hg = true;
            q.dev_no = (uint32_t)std::stoul(devU.substr(2));
        } else if (!devU.empty() && devU[0]=='G') {
            q.use_hg = false;
            q.dev_no = (uint32_t)std::stoul(devU.substr(1));
        } else {
            throw std::runtime_error("U\\device must be G or HG: " + device_part);
        }
    } else {
        throw std::runtime_error("unknown qualified device prefix: " + prefix);
    }
    return q;
}

static void jsonOk(const std::string& extra = "") {
    if (extra.empty()) std::cout << "{\"status\":\"success\"}" << std::endl;
    else std::cout << "{\"status\":\"success\"," << extra << "}" << std::endl;
}
static void jsonErr(const std::string& msg) {
    std::cout << "{\"status\":\"error\",\"message\":\"" << msg << "\"}" << std::endl;
}

int main(int argc, char** argv) {
    if (argc < 4) return 1;
    const char* h = argv[1]; uint16_t p = (uint16_t)std::stoi(argv[2]);
    std::string cmd = argv[3]; std::string ads = (argc > 4) ? argv[4] : "";
    slmp::FrameType fr = slmp::FrameType::Frame3E;
    slmp::CompatibilityMode sr = slmp::CompatibilityMode::Legacy;
    slmp::TargetAddress tr; bool ts = false; std::string md = "word";
    std::string wordDevs, dwordDevs, wordsKv, dwordsKv, bitsKv;
    std::string wordBlocksStr, bitBlocksStr;
    std::vector<std::string> cags;

    for (int i = 5; i < argc; ++i) {
        std::string a = argv[i];
        if (a == "--frame") fr = (argv[++i] == std::string("4e")) ? slmp::FrameType::Frame4E : slmp::FrameType::Frame3E;
        else if (a == "--series") sr = (argv[++i] == std::string("iqr")) ? slmp::CompatibilityMode::iQR : slmp::CompatibilityMode::Legacy;
        else if (a == "--mode") md = argv[++i];
        else if (a == "--target") {
            std::string t = argv[++i]; size_t p1 = t.find(','), p2 = t.find(',',p1+1), p3 = t.find(',',p2+1);
            tr.network=(uint8_t)std::stoi(t.substr(0,p1)); tr.station=(uint8_t)std::stoi(t.substr(p1+1,p2-p1-1));
            tr.module_io=(uint16_t)std::stoi(t.substr(p2+1,p3-p2-1)); tr.multidrop=(uint8_t)std::stoi(t.substr(p3+1));
            ts=true;
        }
        else if (a == "--word-devs") wordDevs = argv[++i];
        else if (a == "--dword-devs") dwordDevs = argv[++i];
        else if (a == "--words") wordsKv = argv[++i];
        else if (a == "--dwords") dwordsKv = argv[++i];
        else if (a == "--bits") bitsKv = argv[++i];
        else if (a == "--word-blocks") wordBlocksStr = argv[++i];
        else if (a == "--bit-blocks") bitBlocksStr = argv[++i];
        else cags.push_back(a);
    }

    SocketTransport transport; uint8_t tx[4096], rx[4096];
    slmp::SlmpClient client(transport, tx, sizeof(tx), rx, sizeof(rx));
    client.setFrameType(fr); client.setCompatibilityMode(sr); if (ts) client.setTarget(tr);
    if (!client.connect(h, p)) { jsonErr("connect failed"); return 0; }

    try {
        // --- Basic read/write ---
        if (cmd == "read-type") {
            slmp::TypeNameInfo info;
            if (client.readTypeName(info) == slmp::Error::Ok)
                std::cout << "{\"status\":\"success\",\"model\":\"" << info.model << "\",\"model_code\":\"0x"
                          << std::hex << std::setw(4) << std::setfill('0') << info.model_code << std::dec << "\"}" << std::endl;
            else jsonErr(std::to_string((int)client.lastError()));
        }
        else if (cmd == "remote-run") {
            if (client.remoteRun() == slmp::Error::Ok) jsonOk(); else jsonErr(std::to_string((int)client.lastError()));
        }
        else if (cmd == "remote-stop") {
            if (client.remoteStop() == slmp::Error::Ok) jsonOk(); else jsonErr(std::to_string((int)client.lastError()));
        }
        else if (cmd == "remote-pause") {
            if (client.remotePause() == slmp::Error::Ok) jsonOk(); else jsonErr(std::to_string((int)client.lastError()));
        }
        else if (cmd == "remote-latch-clear") {
            if (client.remoteLatchClear() == slmp::Error::Ok) jsonOk(); else jsonErr(std::to_string((int)client.lastError()));
        }
        else if (cmd == "remote-reset") {
            // expect_response=false to avoid timeout
            if (client.remoteReset(0x0000, false) == slmp::Error::Ok) jsonOk(); else jsonErr(std::to_string((int)client.lastError()));
        }
        else if (cmd == "self-test") {
            std::string data = ads.empty() ? "TEST" : ads;
            uint8_t out[1024]; size_t out_len = 0;
            if (client.selfTestLoopback((const uint8_t*)data.c_str(), data.size(), out, sizeof(out), out_len) == slmp::Error::Ok) {
                std::string echo(out, out + out_len);
                std::cout << "{\"status\":\"success\",\"echo\":\"" << echo << "\"}" << std::endl;
            } else jsonErr(std::to_string((int)client.lastError()));
        }
        else if (cmd == "memory-read") {
            uint32_t head = (uint32_t)parseAutoInt(ads);
            uint16_t wCount = cags.empty() ? 1 : (uint16_t)std::stoi(cags[0]);
            std::vector<uint16_t> v(wCount);
            if (client.readMemoryWords(head, wCount, v.data(), v.size()) == slmp::Error::Ok) {
                std::cout << "{\"status\":\"success\",\"values\":[";
                for (size_t i=0; i<v.size(); ++i) std::cout << v[i] << (i==v.size()-1?"":",");
                std::cout << "]}" << std::endl;
            } else jsonErr(std::to_string((int)client.lastError()));
        }
        else if (cmd == "memory-write") {
            uint32_t head = (uint32_t)parseAutoInt(ads);
            std::vector<uint16_t> v; for (auto& s : cags) v.push_back((uint16_t)std::stoi(s));
            if (client.writeMemoryWords(head, v.data(), v.size()) == slmp::Error::Ok) jsonOk();
            else jsonErr(std::to_string((int)client.lastError()));
        }
        else if (cmd == "extend-unit-read") {
            auto parts = splitStr(ads, ':');
            uint16_t moduleNo = (uint16_t)parseAutoInt(parts[0]);
            uint32_t head = parts.size() > 1 ? (uint32_t)parseAutoInt(parts[1]) : 0;
            uint16_t wCount = cags.empty() ? 1 : (uint16_t)std::stoi(cags[0]);
            std::vector<uint16_t> v(wCount);
            if (client.readExtendUnitWords(head, wCount, moduleNo, v.data(), v.size()) == slmp::Error::Ok) {
                std::cout << "{\"status\":\"success\",\"values\":[";
                for (size_t i=0; i<v.size(); ++i) std::cout << v[i] << (i==v.size()-1?"":",");
                std::cout << "]}" << std::endl;
            } else jsonErr(std::to_string((int)client.lastError()));
        }
        else if (cmd == "extend-unit-write") {
            auto parts = splitStr(ads, ':');
            uint16_t moduleNo = (uint16_t)parseAutoInt(parts[0]);
            uint32_t head = parts.size() > 1 ? (uint32_t)parseAutoInt(parts[1]) : 0;
            std::vector<uint16_t> v; for (auto& s : cags) v.push_back((uint16_t)std::stoi(s));
            if (client.writeExtendUnitWords(head, moduleNo, v.data(), v.size()) == slmp::Error::Ok) jsonOk();
            else jsonErr(std::to_string((int)client.lastError()));
        }
        else if (cmd == "read-ext") {
            auto q = parseQualifiedDevice(ads);
            int count = cags.empty() ? 1 : std::stoi(cags[0]);
            slmp::Error err = slmp::Error::TransportError;
            if (q.is_link_direct) {
                if (md == "bit") {
                    std::unique_ptr<bool[]> v(new bool[count]());
                    err = client.readBitsLinkDirect(q.j_net, q.code, q.dev_no, (uint16_t)count, v.get(), (size_t)count);
                    if (err == slmp::Error::Ok) {
                        std::cout << "{\"status\":\"success\",\"values\":[";
                        for (int i=0; i<count; ++i) std::cout << (v[i]?1:0) << (i==count-1?"":",");
                        std::cout << "]}" << std::endl;
                    } else jsonErr(std::to_string((int)err));
                } else {
                    std::vector<uint16_t> v(count);
                    err = client.readWordsLinkDirect(q.j_net, q.code, q.dev_no, (uint16_t)count, v.data(), v.size());
                    if (err == slmp::Error::Ok) {
                        std::cout << "{\"status\":\"success\",\"values\":[";
                        for (int i=0; i<count; ++i) std::cout << v[i] << (i==count-1?"":",");
                        std::cout << "]}" << std::endl;
                    } else jsonErr(std::to_string((int)err));
                }
            } else {
                if (md == "bit") {
                    std::unique_ptr<bool[]> v(new bool[count]());
                    err = client.readBitsModuleBuf(q.slot, q.use_hg, q.dev_no, (uint16_t)count, v.get(), (size_t)count);
                    if (err == slmp::Error::Ok) {
                        std::cout << "{\"status\":\"success\",\"values\":[";
                        for (int i=0; i<count; ++i) std::cout << (v[i]?1:0) << (i==count-1?"":",");
                        std::cout << "]}" << std::endl;
                    } else jsonErr(std::to_string((int)err));
                } else {
                    std::vector<uint16_t> v(count);
                    err = client.readWordsModuleBuf(q.slot, q.use_hg, q.dev_no, (uint16_t)count, v.data(), v.size());
                    if (err == slmp::Error::Ok) {
                        std::cout << "{\"status\":\"success\",\"values\":[";
                        for (int i=0; i<count; ++i) std::cout << v[i] << (i==count-1?"":",");
                        std::cout << "]}" << std::endl;
                    } else jsonErr(std::to_string((int)err));
                }
            }
        }
        else if (cmd == "write-ext") {
            auto q = parseQualifiedDevice(ads);
            slmp::Error err = slmp::Error::TransportError;
            if (q.is_link_direct) {
                if (md == "bit") {
                    std::unique_ptr<bool[]> v(new bool[cags.size()]);
                    for (size_t i=0; i<cags.size(); ++i) v[i] = (cags[i]=="1");
                    err = client.writeBitsLinkDirect(q.j_net, q.code, q.dev_no, v.get(), cags.size());
                } else {
                    std::vector<uint16_t> v; for (auto& s : cags) v.push_back((uint16_t)std::stoi(s));
                    err = client.writeWordsLinkDirect(q.j_net, q.code, q.dev_no, v.data(), v.size());
                }
            } else {
                if (md == "bit") {
                    std::unique_ptr<bool[]> v(new bool[cags.size()]);
                    for (size_t i=0; i<cags.size(); ++i) v[i] = (cags[i]=="1");
                    err = client.writeBitsModuleBuf(q.slot, q.use_hg, q.dev_no, v.get(), cags.size());
                } else {
                    std::vector<uint16_t> v; for (auto& s : cags) v.push_back((uint16_t)std::stoi(s));
                    err = client.writeWordsModuleBuf(q.slot, q.use_hg, q.dev_no, v.data(), v.size());
                }
            }
            if (err == slmp::Error::Ok) jsonOk(); else jsonErr(std::to_string((int)err));
        }
        else {
            // read / write / random / block
            auto dev = parseDevice(ads);
            if (cmd == "read") {
                int cnt = !cags.empty() ? std::stoi(cags[0]) : 1;
                if (md == "bit") {
                    std::unique_ptr<bool[]> tmp(new bool[cnt]());
                    if (client.readBits(dev, (uint16_t)cnt, tmp.get(), (size_t)cnt) == slmp::Error::Ok) {
                        std::cout << "{\"status\":\"success\",\"values\":[";
                        for(int i=0;i<cnt;++i) std::cout<<(tmp[i]?1:0)<<(i==cnt-1?"":",");
                        std::cout << "]}" << std::endl;
                    } else jsonErr(std::to_string((int)client.lastError()));
                } else if (md == "dword") {
                    std::vector<uint32_t> v(cnt);
                    if (client.readDWords(dev,(uint16_t)cnt,v.data(),v.size())==slmp::Error::Ok) {
                        std::cout<<"{\"status\":\"success\",\"values\":[";
                        for(int i=0;i<cnt;++i) std::cout<<v[i]<<(i==cnt-1?"":",");
                        std::cout<<"]}"<<std::endl;
                    } else jsonErr(std::to_string((int)client.lastError()));
                } else if (md == "float") {
                    std::vector<float> v(cnt);
                    if (client.readFloat32s(dev,(uint16_t)cnt,v.data(),v.size())==slmp::Error::Ok) {
                        std::cout<<"{\"status\":\"success\",\"values\":[";
                        for(int i=0;i<cnt;++i) std::cout<<std::fixed<<std::setprecision(6)<<v[i]<<(i==cnt-1?"":",");
                        std::cout<<"]}"<<std::endl;
                    } else jsonErr(std::to_string((int)client.lastError()));
                } else {
                    std::vector<uint16_t> v(cnt);
                    if (client.readWords(dev,(uint16_t)cnt,v.data(),v.size())==slmp::Error::Ok) {
                        std::cout<<"{\"status\":\"success\",\"values\":[";
                        for(int i=0;i<cnt;++i) std::cout<<v[i]<<(i==cnt-1?"":",");
                        std::cout<<"]}"<<std::endl;
                    } else jsonErr(std::to_string((int)client.lastError()));
                }
            } else if (cmd == "write") {
                if (md == "bit") {
                    std::unique_ptr<bool[]> v(new bool[cags.size()]);
                    for(size_t i=0;i<cags.size();++i) v[i]=(cags[i]=="1");
                    if (client.writeBits(dev,v.get(),cags.size())==slmp::Error::Ok) jsonOk();
                    else jsonErr(std::to_string((int)client.lastError()));
                } else if (md == "dword") {
                    std::vector<uint32_t> v; for(auto& s:cags) v.push_back((uint32_t)std::stoul(s));
                    if (client.writeDWords(dev,v.data(),v.size())==slmp::Error::Ok) jsonOk();
                    else jsonErr(std::to_string((int)client.lastError()));
                } else if (md == "float") {
                    std::vector<float> v; for(auto& s:cags) v.push_back(std::stof(s));
                    if (client.writeFloat32s(dev,v.data(),v.size())==slmp::Error::Ok) jsonOk();
                    else jsonErr(std::to_string((int)client.lastError()));
                } else {
                    std::vector<uint16_t> v; for(auto& s:cags) v.push_back((uint16_t)std::stoi(s));
                    if (client.writeWords(dev,v.data(),v.size())==slmp::Error::Ok) jsonOk();
                    else jsonErr(std::to_string((int)client.lastError()));
                }
            }
            else if (cmd == "random-read") {
                auto wDevList = splitStr(wordDevs, ',');
                auto dwDevList = splitStr(dwordDevs, ',');
                std::vector<slmp::DeviceAddress> wDevs, dwDevs;
                for (auto& s : wDevList) if (!s.empty()) wDevs.push_back(parseDevice(s));
                for (auto& s : dwDevList) if (!s.empty()) dwDevs.push_back(parseDevice(s));
                std::vector<uint16_t> wv(wDevs.size()); std::vector<uint32_t> dwv(dwDevs.size());
                if (client.readRandom(wDevs.data(),wDevs.size(),wv.data(),wv.size(),
                                      dwDevs.data(),dwDevs.size(),dwv.data(),dwv.size())==slmp::Error::Ok) {
                    std::cout<<"{\"status\":\"success\",\"word_values\":[";
                    for(size_t i=0;i<wv.size();++i) std::cout<<wv[i]<<(i==wv.size()-1?"":",");
                    std::cout<<"],\"dword_values\":[";
                    for(size_t i=0;i<dwv.size();++i) std::cout<<dwv[i]<<(i==dwv.size()-1?"":",");
                    std::cout<<"]}"<<std::endl;
                } else jsonErr(std::to_string((int)client.lastError()));
            }
            else if (cmd == "random-write-words") {
                auto wPairs = parseKvPairs(wordsKv);
                auto dwPairs = parseKvPairs(dwordsKv);
                std::vector<slmp::DeviceAddress> wDevs, dwDevs;
                std::vector<uint16_t> wVals; std::vector<uint32_t> dwVals;
                for (auto& p : wPairs) { wDevs.push_back(parseDevice(p.first)); wVals.push_back((uint16_t)p.second); }
                for (auto& p : dwPairs) { dwDevs.push_back(parseDevice(p.first)); dwVals.push_back((uint32_t)p.second); }
                if (client.writeRandomWords(wDevs.data(),wVals.data(),wDevs.size(),
                                            dwDevs.data(),dwVals.data(),dwDevs.size())==slmp::Error::Ok) jsonOk();
                else jsonErr(std::to_string((int)client.lastError()));
            }
            else if (cmd == "random-write-bits") {
                auto bPairs = parseKvPairs(bitsKv);
                std::vector<slmp::DeviceAddress> bDevs; std::vector<bool> bVals;
                for (auto& p : bPairs) { bDevs.push_back(parseDevice(p.first)); bVals.push_back(p.second != 0); }
                std::unique_ptr<bool[]> bValArr(new bool[bVals.size()]);
                for (size_t i=0;i<bVals.size();++i) bValArr[i]=bVals[i];
                if (client.writeRandomBits(bDevs.data(),bValArr.get(),bDevs.size())==slmp::Error::Ok) jsonOk();
                else jsonErr(std::to_string((int)client.lastError()));
            }
            else if (cmd == "block-read") {
                auto wPairs = parseDevCountPairs(wordBlocksStr);
                auto bPairs = parseDevCountPairs(bitBlocksStr);
                std::vector<slmp::DeviceBlockRead> wBlocks, bBlocks;
                size_t totalW=0, totalB=0;
                for (auto& p : wPairs) { wBlocks.push_back({parseDevice(p.first),(uint16_t)p.second}); totalW+=p.second; }
                for (auto& p : bPairs) { bBlocks.push_back({parseDevice(p.first),(uint16_t)p.second}); totalB+=p.second; }
                std::vector<uint16_t> wVals(totalW), bVals(totalB);
                if (client.readBlock(wBlocks.data(),wBlocks.size(),bBlocks.data(),bBlocks.size(),
                                     wVals.data(),wVals.size(),bVals.data(),bVals.size())==slmp::Error::Ok) {
                    std::cout<<"{\"status\":\"success\",\"word_values\":[";
                    for(size_t i=0;i<wVals.size();++i) std::cout<<wVals[i]<<(i==wVals.size()-1?"":",");
                    std::cout<<"],\"bit_values\":[";
                    for(size_t i=0;i<bVals.size();++i) std::cout<<bVals[i]<<(i==bVals.size()-1?"":",");
                    std::cout<<"]}"<<std::endl;
                } else jsonErr(std::to_string((int)client.lastError()));
            }
            else if (cmd == "block-write") {
                auto wPairsV = parseDevValuesPairs(wordBlocksStr);
                auto bPairsV = parseDevValuesPairs(bitBlocksStr);
                // Keep value storage alive
                std::vector<std::vector<uint16_t>> wStore, bStore;
                std::vector<slmp::DeviceBlockWrite> wBlocks, bBlocks;
                for (auto& p : wPairsV) {
                    wStore.push_back({}); for(int v:p.second) wStore.back().push_back((uint16_t)v);
                    wBlocks.push_back({parseDevice(p.first), wStore.back().data(), (uint16_t)wStore.back().size()});
                }
                for (auto& p : bPairsV) {
                    bStore.push_back({}); for(int v:p.second) bStore.back().push_back((uint16_t)v);
                    bBlocks.push_back({parseDevice(p.first), bStore.back().data(), (uint16_t)bStore.back().size()});
                }
                if (client.writeBlock(wBlocks.data(),wBlocks.size(),bBlocks.data(),bBlocks.size())==slmp::Error::Ok) jsonOk();
                else jsonErr(std::to_string((int)client.lastError()));
            }
            else {
                jsonErr("unknown command: " + cmd);
            }
        }
    } catch (const std::exception& e) { jsonErr(e.what()); }
    return 0;
}
