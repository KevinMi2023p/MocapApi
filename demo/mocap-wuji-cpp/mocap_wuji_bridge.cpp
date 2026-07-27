/*
 * Native Axis Studio -> MocapApi -> Wuji Hand 2 bridge.
 *
 * Both SDKs run in this C++17 process.  MocapApi supplies global hand-joint
 * transforms in meters; those are converted to 21 MediaPipe-order landmarks,
 * retargeted by the Wuji C SDK, and (only with --enable-motors) published to
 * the hand.
 *
 * The default is a dry run.  Motor enable is gated on 30 valid frames and a
 * 250 ms tracking watchdog.  Any exception, tracking gap, or SIGINT closes the
 * publisher and disables the hand before disconnecting.
 */

#include <MocapApi/MocapApi.h>
#include <wuji_sdk.h>

#include <algorithm>
#include <array>
#include <atomic>
#include <chrono>
#include <cmath>
#include <condition_variable>
#include <csignal>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <exception>
#include <iomanip>
#include <iostream>
#include <map>
#include <mutex>
#include <optional>
#include <set>
#include <sstream>
#include <stdexcept>
#include <string>
#include <string_view>
#include <thread>
#include <unordered_map>
#include <utility>
#include <vector>

#include <arpa/inet.h>
#include <ifaddrs.h>
#include <net/if.h>
#include <netinet/in.h>

namespace {

using Clock = std::chrono::steady_clock;
using Seconds = std::chrono::duration<double>;

constexpr std::uint16_t kDefaultUdpPort = 8088;
constexpr std::string_view kDefaultRotation = "YXZ";
constexpr double kLoopHz = 120.0;
constexpr double kStartupTimeoutSeconds = 10.0;
constexpr double kTrackingTimeoutSeconds = 0.250;
constexpr std::size_t kCalibrationFrames = 30;
constexpr double kJointStateTimeoutSeconds = 2.0;
constexpr double kEnableTimeoutSeconds = 5.0;
constexpr double kWujiTelemetryTimeoutSeconds = 1.0;
constexpr double kStartupBlendSeconds = 0.5;
constexpr double kMaxCommandMagnitudeRad = 3.14159265358979323846;
constexpr double kMaxCommandStepRad = 0.25;
constexpr float kEffortLimitAmps = 1.5F;
constexpr float kMitKp = 3.0F;
constexpr float kMitKd = 0.05F;
constexpr std::size_t kFingerCount = 5;
constexpr std::size_t kLandmarkCount = 21;
constexpr std::size_t kWujiJointCount = WUJI_HAND_2_JOINT_COUNT;
constexpr std::size_t kMocapEventBufferCapacity = 512;
constexpr double kMinBoneMeters = 0.001;
constexpr double kMaxBoneMeters = 0.20;
constexpr double kMaxWristDistanceMeters = 0.50;

static_assert(kWujiJointCount == 20, "This bridge expects 20 Wuji joints");

constexpr std::array<std::string_view, kFingerCount> kFingerNames{
    "Thumb", "Index", "Middle", "Ring", "Pinky"};
constexpr std::array<std::size_t, kFingerCount> kMediaPipeBase{
    1, 5, 9, 13, 17};
constexpr std::array<double, kFingerCount> kTipRatios{
    0.857451357, 0.879712793, 0.797619078, 0.777477857, 0.944473814};
constexpr std::array<std::uint8_t, kWujiJointCount> kExpectedNids{
    1, 2, 3, 4, 6, 7, 8, 9, 11, 12, 13, 14, 16, 17, 18, 19, 21, 22, 23, 24};

volatile std::sig_atomic_t gStopRequested = 0;

void on_signal(int) {
    gStopRequested = 1;
}

struct Interrupted final {};

class BridgeError : public std::runtime_error {
public:
    using std::runtime_error::runtime_error;
};

class FrameError : public BridgeError {
public:
    using BridgeError::BridgeError;
};

class TrackingError : public BridgeError {
public:
    using BridgeError::BridgeError;
};

void check_interrupted() {
    if (gStopRequested != 0) {
        throw Interrupted{};
    }
}

double now_seconds() {
    return Seconds(Clock::now().time_since_epoch()).count();
}

std::string_view mcp_error_name(MocapApi::EMCPError status) {
    switch (status) {
        case MocapApi::Error_None: return "None";
        case MocapApi::Error_MoreEvent: return "MoreEvent";
        case MocapApi::Error_InsufficientBuffer: return "InsufficientBuffer";
        case MocapApi::Error_InvalidObject: return "InvalidObject";
        case MocapApi::Error_InvalidHandle: return "InvalidHandle";
        case MocapApi::Error_InvalidParameter: return "InvalidParameter";
        case MocapApi::Error_NotSupported: return "NotSupported";
        case MocapApi::Error_IgnoreUDPSetting: return "IgnoreUDPSetting";
        case MocapApi::Error_IgnoreTCPSetting: return "IgnoreTCPSetting";
        case MocapApi::Error_IgnoreBvhSetting: return "IgnoreBvhSetting";
        case MocapApi::Error_JointNotFound: return "JointNotFound";
        case MocapApi::Error_WithoutTransformation: return "WithoutTransformation";
        case MocapApi::Error_NoneMessage: return "NoneMessage";
        case MocapApi::Error_NoneParent: return "NoneParent";
        case MocapApi::Error_NoneChild: return "NoneChild";
        case MocapApi::Error_AddressInUse: return "AddressInUse";
        case MocapApi::Error_ServerNotReady: return "ServerNotReady";
        case MocapApi::Error_ClientNotReady: return "ClientNotReady";
        case MocapApi::Error_IncompleteCommand: return "IncompleteCommand";
        case MocapApi::Error_UDP: return "UDP";
        case MocapApi::Error_TCP: return "TCP";
        case MocapApi::Error_QueuedCommandFaild: return "QueuedCommandFailed";
        case MocapApi::Error_InterfaceIncompatible:
            return "InterfaceIncompatible";
    }
    return "Unknown";
}

std::string mcp_error_message(MocapApi::EMCPError status,
                              std::string_view operation) {
    std::ostringstream out;
    out << operation << " failed with MocapApi "
        << mcp_error_name(status) << " (" << static_cast<int>(status) << ')';
    return out.str();
}

void check_mcp(MocapApi::EMCPError status, std::string_view operation) {
    if (status != MocapApi::Error_None) {
        throw BridgeError(mcp_error_message(status, operation));
    }
}

std::string wuji_error_message(WujiStatus status,
                               std::string_view operation) {
    const char* detail = wuji_last_error();
    std::ostringstream out;
    out << operation << " failed";
    if (detail != nullptr && detail[0] != '\0') {
        out << ": " << detail;
    } else {
        out << " with Wuji SDK error " << static_cast<int>(status);
    }
    return out.str();
}

void check_wuji(WujiStatus status, std::string_view operation) {
    if (status != WUJI_STATUS_OK) {
        throw BridgeError(wuji_error_message(status, operation));
    }
}

enum class Side {
    Left,
    Right,
};

std::string_view side_name(Side side) {
    return side == Side::Left ? "left" : "right";
}

Side parse_side(std::string_view value) {
    if (value == "left") {
        return Side::Left;
    }
    if (value == "right") {
        return Side::Right;
    }
    throw BridgeError("--side must be left or right");
}

Side from_wuji_side(WujiHandedness side) {
    if (side == WUJI_HANDEDNESS_LEFT) {
        return Side::Left;
    }
    if (side == WUJI_HANDEDNESS_RIGHT) {
        return Side::Right;
    }
    throw BridgeError("Wuji Hand 2 returned unknown handedness");
}

struct Config {
    std::uint16_t udp_port = kDefaultUdpPort;
    std::string rotation{kDefaultRotation};
    std::string listen_address;
    std::string avatar_name;
    std::string hand_sn;
    Side diagnostic_side = Side::Right;
    bool side_explicit = false;
    bool mocap_only = false;
    bool enable_motors = false;
};

void print_usage(const char* program) {
    std::cout
        << "Usage: " << program << " [options]\n\n"
        << "Native Axis Studio BVH -> Wuji Hand 2 bridge.\n\n"
        << "Options:\n"
        << "  --udp-port PORT        MocapApi UDP port (default: 8088)\n"
        << "  --bvh-rotation ORDER   XYZ, XZY, YXZ, YZX, ZXY, or ZYX"
           " (default: YXZ)\n"
        << "  --listen-address IP    Bind MocapApi to one local IPv4 address\n"
        << "  --avatar-name NAME     Select an Axis avatar when more than one exists\n"
        << "  --hand-sn SERIAL       Select one Wuji Hand 2 by serial number\n"
        << "  --mocap-only           Validate Axis frames without connecting to Wuji\n"
        << "  --side left|right      Hand side for --mocap-only (default: right)\n"
        << "  --enable-motors        Configure, enable, and command the hand\n"
        << "  -h, --help             Show this help\n\n"
        << "Dry-run is the default; it never configures or enables motors.\n";
}

std::string require_value(int argc, char** argv, int& index,
                          std::string_view option) {
    if (index + 1 >= argc) {
        throw BridgeError(std::string(option) + " requires a value");
    }
    ++index;
    return argv[index];
}

Config parse_args(int argc, char** argv) {
    Config config;
    for (int i = 1; i < argc; ++i) {
        const std::string arg = argv[i];
        if (arg == "-h" || arg == "--help") {
            print_usage(argv[0]);
            std::exit(0);
        }
        if (arg == "--udp-port") {
            const std::string value = require_value(argc, argv, i, arg);
            std::size_t consumed = 0;
            unsigned long parsed = 0;
            try {
                parsed = std::stoul(value, &consumed);
            } catch (const std::exception&) {
                throw BridgeError("--udp-port must be an integer");
            }
            if (consumed != value.size() || parsed == 0 || parsed > 65535) {
                throw BridgeError("--udp-port must be between 1 and 65535");
            }
            config.udp_port = static_cast<std::uint16_t>(parsed);
        } else if (arg == "--bvh-rotation") {
            config.rotation = require_value(argc, argv, i, arg);
            static const std::set<std::string> valid{
                "XYZ", "XZY", "YXZ", "YZX", "ZXY", "ZYX"};
            if (valid.count(config.rotation) == 0) {
                throw BridgeError("--bvh-rotation is not a valid order");
            }
        } else if (arg == "--listen-address") {
            config.listen_address = require_value(argc, argv, i, arg);
            in_addr address{};
            if (inet_pton(AF_INET, config.listen_address.c_str(), &address) != 1) {
                throw BridgeError("--listen-address must be an IPv4 address");
            }
        } else if (arg == "--avatar-name") {
            config.avatar_name = require_value(argc, argv, i, arg);
        } else if (arg == "--hand-sn") {
            config.hand_sn = require_value(argc, argv, i, arg);
        } else if (arg == "--side") {
            config.diagnostic_side =
                parse_side(require_value(argc, argv, i, arg));
            config.side_explicit = true;
        } else if (arg == "--mocap-only") {
            config.mocap_only = true;
        } else if (arg == "--enable-motors") {
            config.enable_motors = true;
        } else {
            throw BridgeError("unknown option: " + arg);
        }
    }
    if (config.mocap_only && config.enable_motors) {
        throw BridgeError("--mocap-only and --enable-motors cannot be combined");
    }
    if (!config.mocap_only && config.side_explicit) {
        throw BridgeError(
            "--side is only valid with --mocap-only; the physical Wuji hand "
            "determines the live side");
    }
    return config;
}

std::vector<std::string> local_ipv4_addresses() {
    ifaddrs* raw = nullptr;
    if (getifaddrs(&raw) != 0) {
        return {};
    }
    std::vector<std::string> result;
    for (ifaddrs* item = raw; item != nullptr; item = item->ifa_next) {
        if (item->ifa_addr == nullptr ||
            item->ifa_addr->sa_family != AF_INET ||
            (item->ifa_flags & IFF_UP) == 0) {
            continue;
        }
        const auto* address =
            reinterpret_cast<const sockaddr_in*>(item->ifa_addr);
        char buffer[INET_ADDRSTRLEN]{};
        if (inet_ntop(AF_INET, &address->sin_addr, buffer, sizeof(buffer)) ==
            nullptr) {
            continue;
        }
        std::ostringstream entry;
        entry << item->ifa_name << '=' << buffer;
        result.push_back(entry.str());
    }
    freeifaddrs(raw);
    return result;
}

struct Vec3 {
    double x = 0.0;
    double y = 0.0;
    double z = 0.0;
};

Vec3 operator+(const Vec3& a, const Vec3& b) {
    return {a.x + b.x, a.y + b.y, a.z + b.z};
}

Vec3 operator-(const Vec3& a, const Vec3& b) {
    return {a.x - b.x, a.y - b.y, a.z - b.z};
}

Vec3 operator*(const Vec3& value, double scale) {
    return {value.x * scale, value.y * scale, value.z * scale};
}

double norm(const Vec3& value) {
    return std::sqrt(value.x * value.x + value.y * value.y +
                     value.z * value.z);
}

bool finite(const Vec3& value) {
    return std::isfinite(value.x) && std::isfinite(value.y) &&
           std::isfinite(value.z);
}

struct Quat {
    double x = 0.0;
    double y = 0.0;
    double z = 0.0;
    double w = 1.0;
};

Quat normalize(Quat value) {
    const double length = std::sqrt(value.x * value.x + value.y * value.y +
                                    value.z * value.z + value.w * value.w);
    if (!std::isfinite(length) || length < 0.5 || length > 1.5) {
        throw FrameError("joint quaternion has an invalid norm");
    }
    value.x /= length;
    value.y /= length;
    value.z /= length;
    value.w /= length;
    return value;
}

Vec3 cross(const Vec3& a, const Vec3& b) {
    return {
        a.y * b.z - a.z * b.y,
        a.z * b.x - a.x * b.z,
        a.x * b.y - a.y * b.x,
    };
}

Vec3 rotate(Quat quaternion, const Vec3& vector) {
    const Quat q = normalize(quaternion);
    const Vec3 xyz{q.x, q.y, q.z};
    return vector + cross(xyz, cross(xyz, vector) + vector * q.w) * 2.0;
}

std::string joint_name(Side side, std::string_view finger = {},
                       int index = 0) {
    std::string name = side == Side::Left ? "LeftHand" : "RightHand";
    if (!finger.empty()) {
        name += finger;
        name += std::to_string(index);
    }
    return name;
}

std::vector<std::string> required_joint_names(Side side) {
    std::vector<std::string> names;
    names.reserve(16);
    names.push_back(joint_name(side));
    for (const std::string_view finger : kFingerNames) {
        for (int index = 1; index <= 3; ++index) {
            names.push_back(joint_name(side, finger, index));
        }
    }
    return names;
}

struct RawHandPose {
    std::string avatar_name;
    std::uint32_t posture_index = 0;
    double received_at = 0.0;
    std::map<std::string, Vec3> positions;
    std::array<Quat, kFingerCount> distal_rotations;
};

struct LandmarkFrame {
    std::string avatar_name;
    std::uint32_t posture_index = 0;
    double received_at = 0.0;
    std::array<float, kLandmarkCount * 3> keypoints{};
};

double median(std::vector<double> values) {
    if (values.empty()) {
        throw FrameError("cannot calibrate a distal length from no samples");
    }
    std::sort(values.begin(), values.end());
    const std::size_t middle = values.size() / 2;
    if (values.size() % 2 == 0) {
        return (values[middle - 1] + values[middle]) * 0.5;
    }
    return values[middle];
}

class LandmarkBuilder {
public:
    explicit LandmarkBuilder(Side side) : side_(side) {}

    bool calibration_ready() const {
        return calibrated_lengths_.has_value();
    }

    std::size_t calibration_count() const {
        return samples_[0].size();
    }

    void reset() {
        for (auto& samples : samples_) {
            samples.clear();
        }
        calibrated_lengths_.reset();
    }

    LandmarkFrame build(const RawHandPose& raw) {
        const std::vector<std::string> expected = required_joint_names(side_);
        for (const std::string& name : expected) {
            const auto found = raw.positions.find(name);
            if (found == raw.positions.end()) {
                throw FrameError("Axis avatar is missing joint " + name);
            }
            if (!finite(found->second)) {
                throw FrameError("Axis joint " + name +
                                 " has a non-finite position");
            }
        }

        std::array<double, kFingerCount> current_lengths{};
        for (std::size_t finger = 0; finger < kFingerCount; ++finger) {
            const Vec3& p2 = raw.positions.at(
                joint_name(side_, kFingerNames[finger], 2));
            const Vec3& p3 = raw.positions.at(
                joint_name(side_, kFingerNames[finger], 3));
            const double length = norm(p3 - p2);
            if (length < kMinBoneMeters || length > kMaxBoneMeters) {
                std::ostringstream message;
                message << kFingerNames[finger] << " distal bone length "
                        << length << " m is invalid";
                throw FrameError(message.str());
            }
            current_lengths[finger] = length;
        }

        if (!calibrated_lengths_.has_value()) {
            for (std::size_t finger = 0; finger < kFingerCount; ++finger) {
                samples_[finger].push_back(current_lengths[finger]);
            }
            if (samples_[0].size() >= kCalibrationFrames) {
                std::array<double, kFingerCount> calibrated{};
                for (std::size_t finger = 0; finger < kFingerCount; ++finger) {
                    calibrated[finger] = median(samples_[finger]);
                }
                calibrated_lengths_ = calibrated;
            }
        }

        std::array<double, kFingerCount> lengths{};
        if (calibrated_lengths_.has_value()) {
            lengths = *calibrated_lengths_;
        } else {
            for (std::size_t finger = 0; finger < kFingerCount; ++finger) {
                lengths[finger] = median(samples_[finger]);
            }
        }

        std::array<Vec3, kLandmarkCount> points{};
        const Vec3 wrist = raw.positions.at(joint_name(side_));
        points[0] = wrist;
        const double side_sign = side_ == Side::Left ? 1.0 : -1.0;

        for (std::size_t finger = 0; finger < kFingerCount; ++finger) {
            const std::size_t base = kMediaPipeBase[finger];
            for (int joint = 1; joint <= 3; ++joint) {
                points[base + static_cast<std::size_t>(joint - 1)] =
                    raw.positions.at(
                        joint_name(side_, kFingerNames[finger], joint));
            }
            const Vec3 p3 = points[base + 2];
            const Vec3 local_tip{
                side_sign * kTipRatios[finger] * lengths[finger], 0.0, 0.0};
            points[base + 3] =
                p3 + rotate(raw.distal_rotations[finger], local_tip);
        }

        for (Vec3& point : points) {
            point = point - wrist;
            if (!finite(point)) {
                throw FrameError("landmarks contain NaN or infinity");
            }
            if (norm(point) > kMaxWristDistanceMeters) {
                throw FrameError(
                    "a hand landmark is more than 0.5 m from the wrist");
            }
        }
        if (norm(points[0]) > 1e-6) {
            throw FrameError("landmarks are not wrist-relative");
        }

        for (std::size_t finger = 0; finger < kFingerCount; ++finger) {
            const std::size_t base = kMediaPipeBase[finger];
            const std::array<std::size_t, 5> chain{
                0, base, base + 1, base + 2, base + 3};
            for (std::size_t segment = 0; segment + 1 < chain.size();
                 ++segment) {
                const double length =
                    norm(points[chain[segment + 1]] - points[chain[segment]]);
                if (length < kMinBoneMeters || length > kMaxBoneMeters) {
                    std::ostringstream message;
                    message << kFingerNames[finger] << " landmark segment "
                            << chain[segment] << "->" << chain[segment + 1]
                            << " is " << length << " m";
                    throw FrameError(message.str());
                }
            }
        }

        LandmarkFrame frame;
        frame.avatar_name = raw.avatar_name;
        frame.posture_index = raw.posture_index;
        frame.received_at = raw.received_at;
        for (std::size_t index = 0; index < points.size(); ++index) {
            frame.keypoints[index * 3 + 0] =
                static_cast<float>(points[index].x);
            frame.keypoints[index * 3 + 1] =
                static_cast<float>(points[index].y);
            frame.keypoints[index * 3 + 2] =
                static_cast<float>(points[index].z);
        }
        return frame;
    }

private:
    Side side_;
    std::array<std::vector<double>, kFingerCount> samples_;
    std::optional<std::array<double, kFingerCount>> calibrated_lengths_;
};

MocapApi::EMCPBvhRotation parse_rotation(std::string_view rotation) {
    if (rotation == "XYZ") return MocapApi::BvhRotation_XYZ;
    if (rotation == "XZY") return MocapApi::BvhRotation_XZY;
    if (rotation == "YXZ") return MocapApi::BvhRotation_YXZ;
    if (rotation == "YZX") return MocapApi::BvhRotation_YZX;
    if (rotation == "ZXY") return MocapApi::BvhRotation_ZXY;
    if (rotation == "ZYX") return MocapApi::BvhRotation_ZYX;
    throw BridgeError("unsupported BVH rotation order");
}

class MocapReceiver {
public:
    MocapReceiver(const Config& config, Side side)
        : config_(config), side_(side) {
        const char* version = MocapApi::MCPGetMocapApiVersionString();
        std::uint32_t major = 0;
        std::uint32_t minor = 0;
        std::uint32_t build = 0;
        std::uint32_t revision = 0;
        MocapApi::MCPGetMocapApiVersion(
            &major, &minor, &build, &revision);
        if (major != 0 || minor != 0 || build != 73) {
            std::ostringstream message;
            message << "this bridge requires MocapApi 0.0.73; loaded "
                    << (version == nullptr ? "unknown" : version);
            throw BridgeError(message.str());
        }

        check_mcp(
            MocapApi::MCPGetGenericInterface(
                MocapApi::IMCPSettings_Version,
                reinterpret_cast<void**>(&settings_api_)),
            "get IMCPSettings");
        check_mcp(
            MocapApi::MCPGetGenericInterface(
                MocapApi::IMCPRenderSettings_Version,
                reinterpret_cast<void**>(&render_api_)),
            "get IMCPRenderSettings");
        check_mcp(
            MocapApi::MCPGetGenericInterface(
                MocapApi::IMCPApplication_Version,
                reinterpret_cast<void**>(&application_api_)),
            "get IMCPApplication");
        check_mcp(
            MocapApi::MCPGetGenericInterface(
                MocapApi::IMCPAvatar_Version,
                reinterpret_cast<void**>(&avatar_api_)),
            "get IMCPAvatar");
        check_mcp(
            MocapApi::MCPGetGenericInterface(
                MocapApi::IMCPJoint_Version,
                reinterpret_cast<void**>(&joint_api_)),
            "get IMCPJoint");

        try {
            check_mcp(settings_api_->CreateSettings(&settings_),
                      "create MocapApi settings");
            if (config_.listen_address.empty()) {
                check_mcp(
                    settings_api_->SetSettingsUDP(config_.udp_port, settings_),
                    "set MocapApi UDP port");
            } else {
                check_mcp(
                    settings_api_->SetSettingsUDPEx(
                        config_.listen_address.c_str(), config_.udp_port,
                        settings_),
                    "set MocapApi UDP address");
            }
            check_mcp(
                settings_api_->SetSettingsBvhData(
                    MocapApi::BvhDataType_Binary, settings_),
                "set binary BVH");
            check_mcp(
                settings_api_->SetSettingsBvhTransformation(
                    MocapApi::BvhTransformation_Enable, settings_),
                "enable BVH displacement");
            check_mcp(
                settings_api_->SetSettingsBvhRotation(
                    parse_rotation(config_.rotation), settings_),
                "set BVH rotation");

            check_mcp(render_api_->CreateRenderSettings(&render_),
                      "create MocapApi render settings");
            check_mcp(
                render_api_->SetUpVector(
                    MocapApi::UpVector_YAxis, 1, render_),
                "set MocapApi up vector");
            check_mcp(
                render_api_->SetFrontVector(
                    MocapApi::FrontVector_ParityEven, 1, render_),
                "set MocapApi front vector");
            check_mcp(
                render_api_->SetCoordSystem(
                    MocapApi::CoordSystem_RightHanded, render_),
                "set MocapApi coordinate system");
            check_mcp(
                render_api_->SetRotatingDirection(
                    MocapApi::RotatingDirection_CounterClockwise, render_),
                "set MocapApi rotation direction");
            check_mcp(
                render_api_->SetUnit(MocapApi::Uint_Meter, render_),
                "set MocapApi units");

            check_mcp(application_api_->CreateApplication(&application_),
                      "create MocapApi application");
            check_mcp(
                application_api_->SetApplicationSettings(
                    settings_, application_),
                "apply MocapApi settings");
            check_mcp(
                application_api_->SetApplicationRenderSettings(
                    render_, application_),
                "apply MocapApi render settings");
            check_mcp(application_api_->OpenApplication(application_),
                      "open MocapApi application");
            open_ = true;
            check_mcp(
                application_api_->RegisterEventHandler(
                    &MocapReceiver::on_mocap_event,
                    reinterpret_cast<std::intptr_t>(this), application_),
                "register MocapApi event handler");
            handler_registered_ = true;
        } catch (...) {
            close();
            throw;
        }

        std::cout << "MocapApi "
                  << (version == nullptr ? "unknown" : version)
                  << " listening on "
                  << (config_.listen_address.empty() ? "0.0.0.0"
                                                     : config_.listen_address)
                  << ':' << config_.udp_port << " (Binary, displacement on, "
                  << config_.rotation << ", meters)\n";
    }

    ~MocapReceiver() {
        close();
    }

    MocapReceiver(const MocapReceiver&) = delete;
    MocapReceiver& operator=(const MocapReceiver&) = delete;

    std::uint64_t total_event_count() const {
        return received_events_.load(std::memory_order_relaxed);
    }

    std::uint64_t avatar_event_count() const {
        return received_avatar_events_.load(std::memory_order_relaxed);
    }

    std::optional<RawHandPose> poll_latest() {
        if (callback_failed_.exchange(false, std::memory_order_relaxed)) {
            throw BridgeError("MocapApi event callback failed");
        }
        if (event_overflow_.exchange(false, std::memory_order_relaxed)) {
            throw BridgeError(
                "MocapApi event buffer overflowed; processing cannot keep up");
        }

        std::optional<MocapApi::MCPAvatarHandle_t> latest;
        std::array<MocapApi::MCPEvent_t, kMocapEventBufferCapacity> events{};
        std::size_t event_count = 0;
        {
            std::lock_guard<std::mutex> lock(event_mutex_);
            event_count = pending_event_count_;
            std::copy_n(pending_events_.begin(), event_count, events.begin());
            pending_event_count_ = 0;
        }

        for (std::size_t event_index = 0; event_index < event_count;
             ++event_index) {
            const MocapApi::MCPEvent_t& event = events[event_index];
            if (event.eventType == MocapApi::MCPEvent_Error) {
                throw BridgeError(mcp_error_message(
                    event.eventData.systemError.error,
                    "MocapApi stream"));
            }
            if (event.eventType != MocapApi::MCPEvent_AvatarUpdated) {
                continue;
            }
            const MocapApi::MCPAvatarHandle_t avatar =
                event.eventData.motionData.avatarHandle;
            const char* raw_name = nullptr;
            check_mcp(avatar_api_->GetAvatarName(&raw_name, avatar),
                      "get Axis avatar name");
            const std::string name =
                raw_name == nullptr ? std::string{} : raw_name;
            seen_avatars_.insert(name);
            if (config_.avatar_name.empty() &&
                seen_avatars_.size() > 1) {
                throw BridgeError(
                    "multiple Axis avatars are present; pass --avatar-name");
            }
            if (!config_.avatar_name.empty() &&
                name != config_.avatar_name) {
                continue;
            }
            latest = avatar;
        }

        if (!latest.has_value()) {
            return std::nullopt;
        }
        return read_pose(*latest);
    }

private:
    static void on_mocap_event(const MocapApi::MCPEvent_t* event,
                               void* custom) noexcept {
        if (event == nullptr || custom == nullptr) {
            return;
        }
        try {
            auto& receiver = *static_cast<MocapReceiver*>(custom);
            receiver.received_events_.fetch_add(1, std::memory_order_relaxed);
            if (event->eventType == MocapApi::MCPEvent_AvatarUpdated) {
                receiver.received_avatar_events_.fetch_add(
                    1, std::memory_order_relaxed);
            }
            std::lock_guard<std::mutex> lock(receiver.event_mutex_);
            if (receiver.pending_event_count_ >=
                receiver.pending_events_.size()) {
                receiver.event_overflow_.store(true, std::memory_order_relaxed);
                // Preserve the newest event for diagnostics while failing
                // closed on the main thread.
                receiver.pending_events_.back() = *event;
                return;
            }
            receiver.pending_events_[receiver.pending_event_count_++] = *event;
        } catch (...) {
            static_cast<MocapReceiver*>(custom)->callback_failed_.store(
                true, std::memory_order_relaxed);
        }
    }

    struct JointCache {
        std::map<std::string, MocapApi::MCPJointHandle_t> handles;
    };

    RawHandPose read_pose(MocapApi::MCPAvatarHandle_t avatar) {
        const char* raw_name = nullptr;
        check_mcp(avatar_api_->GetAvatarName(&raw_name, avatar),
                  "get Axis avatar name");
        const std::string avatar_name =
            raw_name == nullptr ? std::string{} : raw_name;
        std::uint32_t posture = 0;
        check_mcp(
            avatar_api_->GetAvatarPostureIndex(&posture, avatar),
            "get Axis posture index");
        const auto previous = last_postures_.find(avatar_name);
        if (previous != last_postures_.end() && previous->second == posture) {
            return {};
        }
        last_postures_[avatar_name] = posture;

        JointCache& cache = joint_caches_[avatar_name];
        if (cache.handles.empty()) {
            for (const std::string& name : required_joint_names(side_)) {
                MocapApi::MCPJointHandle_t handle = 0;
                const MocapApi::EMCPError status =
                    avatar_api_->GetAvatarJointByName(
                        name.c_str(), &handle, avatar);
                if (status == MocapApi::Error_JointNotFound) {
                    throw BridgeError("Axis avatar " + avatar_name +
                                      " is missing " + name);
                }
                check_mcp(status, "find Axis joint " + name);
                cache.handles.emplace(name, handle);
            }
        }

        RawHandPose pose;
        pose.avatar_name = avatar_name;
        pose.posture_index = posture;
        pose.received_at = now_seconds();
        for (const auto& [name, handle] : cache.handles) {
            float x = 0.0F;
            float y = 0.0F;
            float z = 0.0F;
            check_mcp(
                joint_api_->GetJointGlobalPosition(&x, &y, &z, handle),
                "get global position for " + name);
            pose.positions.emplace(
                name, Vec3{static_cast<double>(x), static_cast<double>(y),
                           static_cast<double>(z)});
        }
        for (std::size_t finger = 0; finger < kFingerCount; ++finger) {
            const std::string name =
                joint_name(side_, kFingerNames[finger], 3);
            const MocapApi::MCPJointHandle_t handle = cache.handles.at(name);
            float x = 0.0F;
            float y = 0.0F;
            float z = 0.0F;
            float w = 1.0F;
            check_mcp(
                joint_api_->GetJointGlobalRotation(&x, &y, &z, &w, handle),
                "get global rotation for " + name);
            pose.distal_rotations[finger] = normalize(
                {static_cast<double>(x), static_cast<double>(y),
                 static_cast<double>(z), static_cast<double>(w)});
        }
        return pose;
    }

    void close() noexcept {
        if (application_api_ != nullptr && application_ != 0) {
            if (open_) {
                if (handler_registered_) {
                    std::intptr_t user_data = 0;
                    const MocapApi::EMCPError handler_status =
                        application_api_->UnregisterEventHandler(
                            &MocapReceiver::on_mocap_event, &user_data,
                            application_);
                    if (handler_status != MocapApi::Error_None) {
                        std::cerr << mcp_error_message(
                                         handler_status,
                                         "unregister MocapApi event handler")
                                  << '\n';
                    }
                    handler_registered_ = false;
                }
                const MocapApi::EMCPError status =
                    application_api_->CloseApplication(application_);
                if (status != MocapApi::Error_None) {
                    std::cerr << mcp_error_message(
                                     status, "close MocapApi application")
                              << '\n';
                }
                open_ = false;
            }
            const MocapApi::EMCPError status =
                application_api_->DestroyApplication(application_);
            if (status != MocapApi::Error_None) {
                std::cerr << mcp_error_message(
                                 status, "destroy MocapApi application")
                          << '\n';
            }
            application_ = 0;
        }
        if (render_api_ != nullptr && render_ != 0) {
            const MocapApi::EMCPError status =
                render_api_->DestroyRenderSettings(render_);
            if (status != MocapApi::Error_None) {
                std::cerr << mcp_error_message(
                                 status, "destroy MocapApi render settings")
                          << '\n';
            }
            render_ = 0;
        }
        if (settings_api_ != nullptr && settings_ != 0) {
            const MocapApi::EMCPError status =
                settings_api_->DestroySettings(settings_);
            if (status != MocapApi::Error_None) {
                std::cerr << mcp_error_message(
                                 status, "destroy MocapApi settings")
                          << '\n';
            }
            settings_ = 0;
        }
    }

    Config config_;
    Side side_;
    MocapApi::IMCPSettings* settings_api_ = nullptr;
    MocapApi::IMCPRenderSettings* render_api_ = nullptr;
    MocapApi::IMCPApplication* application_api_ = nullptr;
    MocapApi::IMCPAvatar* avatar_api_ = nullptr;
    MocapApi::IMCPJoint* joint_api_ = nullptr;
    MocapApi::MCPSettingsHandle_t settings_ = 0;
    MocapApi::MCPRenderSettingsHandle_t render_ = 0;
    MocapApi::MCPApplicationHandle_t application_ = 0;
    bool open_ = false;
    bool handler_registered_ = false;
    std::mutex event_mutex_;
    std::atomic<std::uint64_t> received_events_{0};
    std::atomic<std::uint64_t> received_avatar_events_{0};
    std::atomic<bool> event_overflow_{false};
    std::atomic<bool> callback_failed_{false};
    std::set<std::string> seen_avatars_;
    std::unordered_map<std::string, std::uint32_t> last_postures_;
    std::unordered_map<std::string, JointCache> joint_caches_;
    std::array<MocapApi::MCPEvent_t, kMocapEventBufferCapacity>
        pending_events_{};
    std::size_t pending_event_count_ = 0;
};

class WujiRuntime {
public:
    WujiRuntime() {
        WujiInitOptions options{};
        options.log_level = 3;
        check_wuji(wuji_init(&options), "wuji_init");
        active_ = true;
        std::cout << "Using wuji-sdk " << wuji_version() << '\n';
    }

    ~WujiRuntime() {
        if (active_) {
            wuji_shutdown();
        }
    }

    WujiRuntime(const WujiRuntime&) = delete;
    WujiRuntime& operator=(const WujiRuntime&) = delete;

private:
    bool active_ = false;
};

struct Telemetry {
    std::mutex mutex;
    std::optional<std::array<float, kWujiJointCount>> actual_positions;
    double state_received_at = 0.0;
    double diagnostics_received_at = 0.0;
    bool all_enabled = false;
    bool diagnostics_complete = false;
    std::uint8_t fault_nid = 0;
    std::uint16_t fault_code = 0;
    std::uint32_t fault_status_word = 0;
    bool state_terminal = false;
    bool diagnostics_terminal = false;
};

std::optional<std::size_t> command_index_for_nid(std::uint8_t nid) {
    const auto found =
        std::find(kExpectedNids.begin(), kExpectedNids.end(), nid);
    if (found == kExpectedNids.end()) {
        return std::nullopt;
    }
    return static_cast<std::size_t>(
        std::distance(kExpectedNids.begin(), found));
}

void on_joint_state(WujiFrameKind kind, const WujiJointStateFrame* frame,
                    void* user_data) {
    auto& telemetry = *static_cast<Telemetry*>(user_data);
    std::lock_guard<std::mutex> lock(telemetry.mutex);
    if (kind == WUJI_FRAME_KIND_END || kind == WUJI_FRAME_KIND_ERROR) {
        telemetry.state_terminal = true;
        return;
    }
    if (kind != WUJI_FRAME_KIND_OK || frame == nullptr ||
        frame->joints_len != kWujiJointCount) {
        return;
    }

    std::array<float, kWujiJointCount> positions{};
    std::array<bool, kWujiJointCount> seen{};
    for (std::size_t index = 0; index < frame->joints_len; ++index) {
        const auto command_index =
            command_index_for_nid(frame->joints[index].nid);
        if (!command_index.has_value() ||
            !std::isfinite(frame->joints[index].position)) {
            return;
        }
        positions[*command_index] = frame->joints[index].position;
        seen[*command_index] = true;
    }
    if (std::all_of(seen.begin(), seen.end(), [](bool value) { return value; })) {
        telemetry.actual_positions = positions;
        telemetry.state_received_at = now_seconds();
    }
}

void on_joint_diagnostics(WujiFrameKind kind,
                          const WujiJointDiagnosticsFrame* frame,
                          void* user_data) {
    auto& telemetry = *static_cast<Telemetry*>(user_data);
    std::lock_guard<std::mutex> lock(telemetry.mutex);
    if (kind == WUJI_FRAME_KIND_END || kind == WUJI_FRAME_KIND_ERROR) {
        telemetry.diagnostics_terminal = true;
        return;
    }
    if (kind != WUJI_FRAME_KIND_OK || frame == nullptr ||
        frame->joints_len != kWujiJointCount) {
        return;
    }

    std::array<bool, kWujiJointCount> seen{};
    bool enabled = true;
    telemetry.fault_nid = 0;
    telemetry.fault_code = 0;
    telemetry.fault_status_word = 0;
    for (std::size_t index = 0; index < frame->joints_len; ++index) {
        const auto command_index =
            command_index_for_nid(frame->joints[index].nid);
        if (!command_index.has_value()) {
            enabled = false;
            break;
        }
        seen[*command_index] = true;
        if ((frame->joints[index].status_word & 0x3U) != 2U ||
            frame->joints[index].error_code_current != 0U) {
            enabled = false;
            if (telemetry.fault_nid == 0) {
                telemetry.fault_nid = frame->joints[index].nid;
                telemetry.fault_code =
                    frame->joints[index].error_code_current;
                telemetry.fault_status_word =
                    frame->joints[index].status_word;
            }
        }
    }
    telemetry.diagnostics_complete =
        std::all_of(seen.begin(), seen.end(), [](bool value) { return value; });
    telemetry.all_enabled = enabled && telemetry.diagnostics_complete;
    telemetry.diagnostics_received_at = now_seconds();
}

class WujiTarget {
public:
    explicit WujiTarget(const std::string& requested_sn) {
        WujiDiscovered* raw_devices = nullptr;
        std::size_t count = 0;
        check_wuji(wuji_scan(&raw_devices, &count), "wuji_scan");

        std::vector<WujiDiscovered> matches;
        for (std::size_t index = 0; index < count; ++index) {
            if (raw_devices[index].device_id !=
                WUJI_DEVICE_TYPE_WUJI_HAND_2) {
                continue;
            }
            if (!requested_sn.empty() &&
                requested_sn != raw_devices[index].serial_number) {
                continue;
            }
            matches.push_back(raw_devices[index]);
        }
        wuji_discovered_free(raw_devices, count);

        if (matches.size() != 1) {
            std::ostringstream message;
            message << "expected exactly one ";
            if (!requested_sn.empty()) {
                message << "Wuji Hand 2 matching " << requested_sn << "; found ";
            } else {
                message << "Wuji Hand 2; found ";
            }
            message << matches.size();
            throw BridgeError(message.str());
        }

        serial_ = matches[0].serial_number;
        WujiConnectTarget target{};
        target.kind = WUJI_CONNECT_TARGET_KIND_SN;
        target.value = serial_.c_str();
        try {
            check_wuji(
                wuji_connect(&target, "mocap_wuji_hand", nullptr, &device_),
                "connect Wuji Hand 2");

            WujiHandedness handedness = WUJI_HANDEDNESS_LEFT;
            check_wuji(
                wuji_hand_2_get_handedness(device_, &handedness),
                "read Wuji handedness");
            side_ = from_wuji_side(handedness);

            std::uint8_t online_count = 0;
            check_wuji(
                wuji_hand_2_online_joints_count(device_, &online_count),
                "read Wuji online joint count");
            std::array<float, kWujiJointCount> limits{};
            std::uint32_t online_mask = 0;
            check_wuji(
                wuji_hand_2_get_all_effort_limit(
                    device_, limits.data(), &online_mask),
                "read Wuji online joint mask");
            constexpr std::uint32_t expected_mask =
                (1U << kWujiJointCount) - 1U;
            if (online_count != kWujiJointCount ||
                (online_mask & expected_mask) != expected_mask) {
                std::ostringstream message;
                message << "Wuji Hand 2 has "
                        << static_cast<unsigned int>(online_count)
                        << "/20 joints online (mask 0x" << std::hex
                        << online_mask << ')';
                throw BridgeError(message.str());
            }

            check_wuji(
                wuji_retarget_session_create(
                    WUJI_HAND_MODEL_WUJI_HAND2,
                    static_cast<std::int32_t>(handedness), &retarget_),
                "create Wuji retarget session");
        } catch (...) {
            close();
            throw;
        }

        std::cout << "Connected read-only to " << serial_ << " ("
                  << side_name(side_) << ", 20 joints online)\n";
    }

    ~WujiTarget() {
        close();
    }

    WujiTarget(const WujiTarget&) = delete;
    WujiTarget& operator=(const WujiTarget&) = delete;

    Side side() const {
        return side_;
    }

    std::array<float, kWujiJointCount> retarget(
        const LandmarkFrame& frame) {
        std::array<float, kWujiJointCount> qpos{};
        const WujiStatus status = wuji_retarget_session_step(
            retarget_, frame.keypoints.data(), qpos.data());
        if (status == WUJI_STATUS_ERR_ALGORITHM) {
            throw FrameError(wuji_error_message(
                status, "retarget Axis hand frame"));
        }
        check_wuji(status, "retarget Axis hand");
        if (!std::all_of(qpos.begin(), qpos.end(),
                         [](float value) { return std::isfinite(value); })) {
            throw FrameError("Wuji retargeter returned NaN or infinity");
        }
        return qpos;
    }

    void reset_retarget() {
        check_wuji(wuji_retarget_session_reset(retarget_),
                   "reset Wuji retarget session");
    }

    void prepare_actuation() {
        // Arm fail-closed cleanup before changing any control resource.  Also
        // force a known disabled baseline in case a previous process exited
        // without completing its cleanup.
        enable_attempted_ = true;
        check_wuji(wuji_hand_2_disable(device_, nullptr),
                   "establish disabled Wuji Hand 2 state");

        check_wuji(
            wuji_hand_2_set_all_effort_limit(
                device_, kEffortLimitAmps),
            "set Wuji effort limit");
        std::array<float, kWujiJointCount> kp{};
        std::array<float, kWujiJointCount> kd{};
        kp.fill(kMitKp);
        kd.fill(kMitKd);
        check_wuji(
            wuji_hand_2_set_all_mit_params(
                device_, kp.data(), kd.data()),
            "set Wuji MIT parameters");

        // Open every stream that can fail before enabling motors.
        check_wuji(
            wuji_hand_2_joint_command_publish(device_, &publisher_),
            "open Wuji joint command publisher");
        check_wuji(
            wuji_hand_2_subscribe_joint_states(
                device_, on_joint_state, &telemetry_, &joint_state_sub_),
            "subscribe Wuji joint states");
        check_wuji(
            wuji_hand_2_subscribe_joint_diagnostics(
                device_, on_joint_diagnostics, &telemetry_,
                &diagnostics_sub_),
            "subscribe Wuji joint diagnostics");
    }

    std::optional<std::array<float, kWujiJointCount>> actual_positions() {
        std::lock_guard<std::mutex> lock(telemetry_.mutex);
        if (telemetry_.state_terminal) {
            throw BridgeError("Wuji joint-state stream ended");
        }
        return telemetry_.actual_positions;
    }

    bool all_enabled() {
        std::lock_guard<std::mutex> lock(telemetry_.mutex);
        if (telemetry_.diagnostics_terminal) {
            throw BridgeError("Wuji diagnostics stream ended");
        }
        return telemetry_.all_enabled;
    }

    void require_healthy(double now) {
        std::lock_guard<std::mutex> lock(telemetry_.mutex);
        if (telemetry_.state_terminal) {
            throw BridgeError("Wuji joint-state stream ended");
        }
        if (telemetry_.diagnostics_terminal) {
            throw BridgeError("Wuji diagnostics stream ended");
        }
        if (!telemetry_.actual_positions.has_value() ||
            now - telemetry_.state_received_at >
                kWujiTelemetryTimeoutSeconds) {
            throw BridgeError("Wuji joint-state telemetry is stale");
        }
        if (!telemetry_.diagnostics_complete ||
            now - telemetry_.diagnostics_received_at >
                kWujiTelemetryTimeoutSeconds) {
            throw BridgeError("Wuji motor diagnostics are stale");
        }
        if (!telemetry_.all_enabled) {
            std::ostringstream message;
            message << "a Wuji motor is faulted, offline, or no longer Enabled";
            if (telemetry_.fault_nid != 0) {
                message << " (nid="
                        << static_cast<unsigned int>(telemetry_.fault_nid)
                        << ", status=0x" << std::hex
                        << telemetry_.fault_status_word << ", error=0x"
                        << telemetry_.fault_code << ')';
            }
            throw BridgeError(message.str());
        }
    }

    void enable() {
        check_wuji(wuji_hand_2_enable(device_, nullptr),
                   "enable Wuji Hand 2");
        enabled_ = true;
    }

    void seed_hold(const std::array<float, kWujiJointCount>& qpos) {
        if (publisher_ == nullptr) {
            throw BridgeError("Wuji publisher is not ready for a hold command");
        }
        publish(qpos);
    }

    void send(const std::array<float, kWujiJointCount>& qpos) {
        if (!enabled_ || publisher_ == nullptr) {
            throw BridgeError("Wuji motors are not ready for commands");
        }
        publish(qpos);
    }

private:
    void publish(const std::array<float, kWujiJointCount>& qpos) {
        std::array<WujiJointCommand, kWujiJointCount> commands{};
        for (std::size_t index = 0; index < commands.size(); ++index) {
            if (!std::isfinite(qpos[index])) {
                throw FrameError("refusing to publish a non-finite command");
            }
            if (std::abs(static_cast<double>(qpos[index])) >
                kMaxCommandMagnitudeRad) {
                throw FrameError(
                    "refusing a Wuji command outside the absolute safety bound");
            }
            if (last_published_.has_value() &&
                std::abs(static_cast<double>(
                    qpos[index] - (*last_published_)[index])) >
                    kMaxCommandStepRad) {
                throw FrameError(
                    "refusing an abrupt Wuji command jump");
            }
            commands[index].position = qpos[index];
            commands[index].velocity = 0.0F;
            commands[index].effort = 0.0F;
        }
        check_wuji(
            wuji_joint_command_publisher_send(
                publisher_, commands.data()),
            "publish Wuji joint command");
        last_published_ = qpos;
    }

    void close() noexcept {
        if (publisher_ != nullptr) {
            wuji_joint_command_publisher_close(publisher_);
            publisher_ = nullptr;
        }
        if (device_ != nullptr && (enabled_ || enable_attempted_)) {
            const WujiStatus status =
                wuji_hand_2_disable(device_, nullptr);
            if (status != WUJI_STATUS_OK) {
                try {
                    std::cerr << wuji_error_message(
                                     status, "disable Wuji Hand 2")
                              << '\n';
                    const WujiStatus stop_status =
                        wuji_hand_2_emergency_stop(device_);
                    if (stop_status != WUJI_STATUS_OK) {
                        std::cerr << wuji_error_message(
                                         stop_status,
                                         "emergency-stop Wuji Hand 2")
                                  << '\n';
                    }
                } catch (...) {
                    // Cleanup must continue even if formatting fails.
                }
            }
            enabled_ = false;
            enable_attempted_ = false;
        }
        if (joint_state_sub_ != nullptr) {
            wuji_sub_close(joint_state_sub_);
            joint_state_sub_ = nullptr;
        }
        if (diagnostics_sub_ != nullptr) {
            wuji_sub_close(diagnostics_sub_);
            diagnostics_sub_ = nullptr;
        }
        if (retarget_ != nullptr) {
            wuji_retarget_session_free(retarget_);
            retarget_ = nullptr;
        }
        if (device_ != nullptr) {
            const WujiStatus status = wuji_dev_disconnect(device_);
            if (status != WUJI_STATUS_OK) {
                try {
                    std::cerr << wuji_error_message(
                                     status, "disconnect Wuji Hand 2")
                              << '\n';
                } catch (...) {
                }
            }
            wuji_dev_release(device_);
            device_ = nullptr;
        }
    }

    WujiDevice* device_ = nullptr;
    WujiRetargetSession* retarget_ = nullptr;
    WujiJointCommandPublisher* publisher_ = nullptr;
    WujiSub* joint_state_sub_ = nullptr;
    WujiSub* diagnostics_sub_ = nullptr;
    Telemetry telemetry_;
    std::string serial_;
    Side side_ = Side::Right;
    bool enabled_ = false;
    bool enable_attempted_ = false;
    std::optional<std::array<float, kWujiJointCount>> last_published_;
};

class FreshnessWatchdog {
public:
    void mark_valid(double timestamp) {
        last_valid_ = timestamp;
    }

    bool has_valid_frame() const {
        return last_valid_.has_value();
    }

    void require_fresh(double now) const {
        if (!last_valid_.has_value()) {
            return;
        }
        const double age = now - *last_valid_;
        if (age >= kTrackingTimeoutSeconds) {
            std::ostringstream message;
            message << "tracking frame is stale (" << std::fixed
                    << std::setprecision(1) << age * 1000.0
                    << " ms; limit "
                    << kTrackingTimeoutSeconds * 1000.0 << " ms)";
            throw TrackingError(message.str());
        }
    }

private:
    std::optional<double> last_valid_;
};

double smoothstep(double value) {
    value = std::clamp(value, 0.0, 1.0);
    return value * value * (3.0 - 2.0 * value);
}

std::array<float, kWujiJointCount> blend(
    const std::array<float, kWujiJointCount>& from,
    const std::array<float, kWujiJointCount>& to,
    double amount) {
    const float alpha = static_cast<float>(smoothstep(amount));
    std::array<float, kWujiJointCount> result{};
    for (std::size_t index = 0; index < result.size(); ++index) {
        result[index] = from[index] + (to[index] - from[index]) * alpha;
    }
    return result;
}

void sleep_briefly() {
    std::this_thread::sleep_for(std::chrono::milliseconds(1));
}

int run_bridge(const Config& config) {
    const std::vector<std::string> addresses = local_ipv4_addresses();
    std::cout << "Local IPv4 addresses:";
    if (addresses.empty()) {
        std::cout << " (unavailable)";
    } else {
        for (const std::string& address : addresses) {
            std::cout << ' ' << address;
        }
    }
    std::cout << '\n'
              << "Axis Destination Address must be the address reachable from "
                 "the Axis laptop; Destination Port must be "
              << config.udp_port << ".\n";

    std::optional<WujiRuntime> runtime;
    std::optional<WujiTarget> target;
    Side side = config.diagnostic_side;
    if (!config.mocap_only) {
        runtime.emplace();
        target.emplace(config.hand_sn);
        side = target->side();
    } else {
        std::cout << "Mocap-only diagnostic mode (" << side_name(side)
                  << " hand); Wuji SDK will not be initialized.\n";
    }

    MocapReceiver source(config, side);
    LandmarkBuilder builder(side);
    FreshnessWatchdog watchdog;
    std::optional<std::array<float, kWujiJointCount>> latest_qpos;
    double startup_started = now_seconds();
    double last_invalid_log = 0.0;

    if (config.enable_motors) {
        std::cout << "Motor control requested; motors remain disabled until "
                  << kCalibrationFrames << " valid frames pass preflight.\n";
    } else if (!config.mocap_only) {
        std::cout << "Dry-run active: motors will not be configured, enabled, "
                     "or commanded.\n";
    }

    auto pump = [&](bool preflight) -> bool {
        const std::optional<RawHandPose> raw = source.poll_latest();
        if (!raw.has_value() || raw->avatar_name.empty()) {
            watchdog.require_fresh(now_seconds());
            return false;
        }
        try {
            const LandmarkFrame frame = builder.build(*raw);
            if (target.has_value()) {
                latest_qpos = target->retarget(frame);
            }
            watchdog.mark_valid(frame.received_at);
            return true;
        } catch (const FrameError& error) {
            if (!preflight) {
                throw;
            }
            builder.reset();
            if (target.has_value()) {
                target->reset_retarget();
            }
            const double now = now_seconds();
            if (now - last_invalid_log >= 1.0) {
                std::cerr << "Rejected mocap frame during preflight: "
                          << error.what() << '\n';
                last_invalid_log = now;
            }
            watchdog.require_fresh(now);
            return false;
        }
    };

    while (!builder.calibration_ready() ||
           (!config.mocap_only && !latest_qpos.has_value())) {
        check_interrupted();
        const double now = now_seconds();
        if (now - startup_started >= kStartupTimeoutSeconds &&
            !watchdog.has_valid_frame()) {
            std::ostringstream message;
            message
                << "no valid Axis AvatarUpdated hand frame arrived within "
                << kStartupTimeoutSeconds << " seconds on "
                << (config.listen_address.empty() ? "0.0.0.0"
                                                  : config.listen_address)
                << ':' << config.udp_port << " (" << config.rotation
                << "). Saw " << source.total_event_count() << " total events / "
                << source.avatar_event_count()
                << " avatar events. Check the hotspot-facing Destination "
                   "Address, UDP port, Binary format, Old Header off, "
                   "Displacement on, and stop any other listener on this port";
            throw TrackingError(message.str());
        }
        pump(true);
        watchdog.require_fresh(now_seconds());
        sleep_briefly();
    }

    std::cout << "Preflight complete: " << kCalibrationFrames
              << " valid frames, " << side_name(side)
              << " avatar side.\n";

    std::optional<std::array<float, kWujiJointCount>> blend_from;
    double blend_started = 0.0;
    if (config.enable_motors) {
        target->prepare_actuation();

        const double state_deadline =
            now_seconds() + kJointStateTimeoutSeconds;
        while (!blend_from.has_value()) {
            check_interrupted();
            pump(false);
            blend_from = target->actual_positions();
            watchdog.require_fresh(now_seconds());
            if (now_seconds() >= state_deadline) {
                throw BridgeError(
                    "timed out waiting for a complete Wuji joint-state frame");
            }
            sleep_briefly();
        }

        target->enable();
        // Publish the measured pose immediately after the enable request and
        // keep holding it while firmware transitions to Enabled.  This avoids
        // leaving a stale/zero target active during the diagnostics wait.
        target->seed_hold(*blend_from);
        const double enable_deadline =
            now_seconds() + kEnableTimeoutSeconds;
        Clock::time_point next_hold = Clock::now();
        while (!target->all_enabled()) {
            check_interrupted();
            pump(false);
            watchdog.require_fresh(now_seconds());
            if (Clock::now() >= next_hold) {
                target->seed_hold(*blend_from);
                next_hold =
                    Clock::now() +
                    std::chrono::duration_cast<Clock::duration>(
                        Seconds{1.0 / kLoopHz});
            }
            if (now_seconds() >= enable_deadline) {
                throw BridgeError(
                    "timed out waiting for all Wuji motors to report Enabled");
            }
            sleep_briefly();
        }
        std::cout << "All motors enabled; beginning tracked command stream.\n";
        blend_started = now_seconds();
    }

    const Seconds frame_budget{1.0 / kLoopHz};
    Clock::time_point next_tick = Clock::now();
    double report_started = now_seconds();
    std::uint64_t report_frames = 0;
    while (true) {
        check_interrupted();
        const Clock::time_point now = Clock::now();
        if (now < next_tick) {
            watchdog.require_fresh(now_seconds());
            std::this_thread::sleep_for(
                std::min(std::chrono::milliseconds(1),
                         std::chrono::duration_cast<std::chrono::milliseconds>(
                             next_tick - now)));
            continue;
        }
        next_tick = now +
                    std::chrono::duration_cast<Clock::duration>(frame_budget);

        if (pump(false)) {
            ++report_frames;
        }
        const double command_time = now_seconds();
        watchdog.require_fresh(command_time);

        if (config.enable_motors) {
            target->require_healthy(command_time);
            std::array<float, kWujiJointCount> command = *latest_qpos;
            if (blend_from.has_value()) {
                const double amount =
                    (command_time - blend_started) / kStartupBlendSeconds;
                command = blend(*blend_from, *latest_qpos, amount);
                if (amount >= 1.0) {
                    blend_from.reset();
                }
            }
            target->send(command);
        }

        if (command_time - report_started >= 1.0) {
            const double elapsed = command_time - report_started;
            const char* mode = config.mocap_only
                                   ? "MOCAP"
                                   : (config.enable_motors ? "LIVE" : "DRY-RUN");
            std::cout << mode << " side=" << side_name(side)
                      << " input=" << std::fixed << std::setprecision(1)
                      << static_cast<double>(report_frames) / elapsed << "Hz";
            if (latest_qpos.has_value()) {
                const auto [minimum, maximum] =
                    std::minmax_element(
                        latest_qpos->begin(), latest_qpos->end());
                std::cout << " qpos=[" << std::showpos << std::setprecision(3)
                          << *minimum << ", " << *maximum << std::noshowpos
                          << ']';
            }
            std::cout << '\n';
            report_started = command_time;
            report_frames = 0;
        }
    }
}

}  // namespace

int main(int argc, char** argv) {
    std::signal(SIGINT, on_signal);
    std::signal(SIGTERM, on_signal);

    try {
        return run_bridge(parse_args(argc, argv));
    } catch (const Interrupted&) {
        std::cout << "\nStopped by operator.\n";
        return 130;
    } catch (const TrackingError& error) {
        std::cerr << "Tracking stopped: " << error.what() << '\n';
        return 2;
    } catch (const std::exception& error) {
        std::cerr << "Bridge failed: " << error.what() << '\n';
        return 1;
    }
}
