/*
 * ESP32-S3 BLE proxy for Xiaomi Smart Temperature and Humidity Monitor 3 Mini.
 *
 * USB NDJSON remains enabled at all times.  When Wi-Fi is configured, the
 * same advertisements are queued and sent in batches to the Mac service.
 * Wi-Fi credentials and the proxy identity are stored in NVS and are supplied
 * through one JSON configuration line over USB (see tools/configure_proxy.py).
 */
#include <Arduino.h>
#include <ArduinoJson.h>
#include <HTTPClient.h>
#include <NimBLEDevice.h>
#include <Preferences.h>
#include <WiFi.h>

static String hexEncode(const std::string &value) {
  const char *digits = "0123456789abcdef";
  String out;
  for (unsigned char c : value) { out += digits[c >> 4]; out += digits[c & 0x0f]; }
  return out;
}

static String jsonString(const String &value) {
  String out = "\"";
  for (size_t i = 0; i < value.length(); i++) {
    char c = value[i];
    if (c == '\\' || c == '\"') { out += '\\'; }
    if (c >= 32) out += c;
  }
  out += "\"";
  return out;
}

struct ProxyConfig {
  String ssid;
  String password;
  String serverHost;
  uint16_t serverPort = 8787;
  String proxyId;
  String ingestToken;
};

static ProxyConfig config;
static Preferences preferences;
static NimBLEScan *scan;
static portMUX_TYPE queueMux = portMUX_INITIALIZER_UNLOCKED;
static String pendingLines[24];
static uint8_t queueHead = 0;
static uint8_t queueTail = 0;
static uint8_t queueCount = 0;
static uint32_t lastWifiAttempt = 0;
static uint32_t lastUpload = 0;

static String defaultProxyId() {
  String mac = WiFi.macAddress();
  mac.replace(":", "");
  mac.toLowerCase();
  return String("proxy-") + mac;
}

static void loadConfig() {
  preferences.begin("home-monitor", true);
  config.ssid = preferences.getString("ssid", "");
  config.password = preferences.getString("password", "");
  config.serverHost = preferences.getString("host", "");
  config.serverPort = preferences.getUShort("port", 8787);
  config.proxyId = preferences.getString("proxy", "");
  config.ingestToken = preferences.getString("token", "");
  preferences.end();
  if (!config.proxyId.length()) config.proxyId = defaultProxyId();
}

static void saveConfig(JsonObjectConst object) {
  if (object["wifi_ssid"].is<const char *>()) config.ssid = object["wifi_ssid"].as<String>();
  if (object["wifi_password"].is<const char *>()) config.password = object["wifi_password"].as<String>();
  if (object["server_host"].is<const char *>()) config.serverHost = object["server_host"].as<String>();
  if (object["server_port"].is<uint16_t>()) config.serverPort = object["server_port"].as<uint16_t>();
  if (object["proxy_id"].is<const char *>()) config.proxyId = object["proxy_id"].as<String>();
  if (object["ingest_token"].is<const char *>()) config.ingestToken = object["ingest_token"].as<String>();
  if (!config.proxyId.length()) config.proxyId = defaultProxyId();

  preferences.begin("home-monitor", false);
  preferences.putString("ssid", config.ssid);
  preferences.putString("password", config.password);
  preferences.putString("host", config.serverHost);
  preferences.putUShort("port", config.serverPort);
  preferences.putString("proxy", config.proxyId);
  preferences.putString("token", config.ingestToken);
  preferences.end();

  Serial.print("{\"type\":\"config\",\"ok\":true,\"proxy_id\":");
  Serial.print(jsonString(config.proxyId));
  Serial.println("}");
  WiFi.disconnect(true, true);
  lastWifiAttempt = 0;
}

static void processSerialCommands() {
  static String line;
  while (Serial.available()) {
    char c = static_cast<char>(Serial.read());
    if (c == '\n' || c == '\r') {
      if (!line.length()) continue;
      StaticJsonDocument<1024> document;
      DeserializationError error = deserializeJson(document, line);
      if (!error && document["type"] == "configure") saveConfig(document.as<JsonObjectConst>());
      else if (line.length() > 0) Serial.println("{\"type\":\"config\",\"ok\":false,\"error\":\"expected configure JSON\"}");
      line = "";
    } else if (line.length() < 1200) {
      line += c;
    }
  }
}

static void ensureWiFi() {
  if (!config.ssid.length() || !config.serverHost.length()) return;
  if (WiFi.status() == WL_CONNECTED) return;
  if (millis() - lastWifiAttempt < 10000) return;
  lastWifiAttempt = millis();
  WiFi.mode(WIFI_STA);
  WiFi.begin(config.ssid.c_str(), config.password.c_str());
  Serial.println("{\"type\":\"wifi\",\"state\":\"connecting\"}");
}

static void enqueueForWiFi(const String &line) {
  portENTER_CRITICAL(&queueMux);
  if (queueCount < 24) {
    pendingLines[queueTail] = line;
    queueTail = (queueTail + 1) % 24;
    queueCount++;
  }
  portEXIT_CRITICAL(&queueMux);
}

static bool takeBatch(String &body, uint8_t &taken) {
  String lines[8];
  portENTER_CRITICAL(&queueMux);
  taken = min<uint8_t>(queueCount, 8);
  for (uint8_t i = 0; i < taken; i++) {
    lines[i] = pendingLines[(queueHead + i) % 24];
  }
  portEXIT_CRITICAL(&queueMux);
  if (!taken) return false;

  body = String("{\"proxy_id\":") + jsonString(config.proxyId) + ",\"advertisements\":[";
  for (uint8_t i = 0; i < taken; i++) {
    if (i) body += ',';
    body += lines[i];
  }
  body += "]}";
  return true;
}

static void finishBatch(uint8_t taken, bool success) {
  if (!success) return;
  portENTER_CRITICAL(&queueMux);
  queueHead = (queueHead + taken) % 24;
  queueCount -= taken;
  portEXIT_CRITICAL(&queueMux);
}

static void uploadQueued() {
  if (WiFi.status() != WL_CONNECTED || !config.serverHost.length()) return;
  if (millis() - lastUpload < 1500) return;
  lastUpload = millis();
  String body;
  uint8_t taken = 0;
  if (!takeBatch(body, taken)) return;

  HTTPClient http;
  String url = String("http://") + config.serverHost + ":" + config.serverPort + "/api/ingest";
  http.setConnectTimeout(2500);
  http.setTimeout(4000);
  if (!http.begin(url)) return;
  http.addHeader("Content-Type", "application/json");
  if (config.ingestToken.length()) http.addHeader("X-Home-Monitor-Token", config.ingestToken);
  int status = http.POST(body);
  bool success = status >= 200 && status < 300;
  http.end();
  if (success) {
    finishBatch(taken, true);
    Serial.print("{\"type\":\"upload\",\"ok\":true,\"count\":");
    Serial.print(taken);
    Serial.println("}");
  }
}

class AdvertisedCallback : public NimBLEScanCallbacks {
  void onResult(const NimBLEAdvertisedDevice *device) override {
    String address = String(device->getAddress().toString().c_str());
    String name = device->haveName() ? String(device->getName().c_str()) : "";
    String line;
    line.reserve(700);
    line += "{\"type\":\"advertisement\",\"ts\":";
    line += String(static_cast<double>(millis()) / 1000.0, 3);
    line += ",\"address\":"; line += jsonString(address);
    line += ",\"rssi\":"; line += String(device->getRSSI());
    line += ",\"name\":"; line += jsonString(name);
    line += ",\"manufacturer_data\":{";
    if (device->haveManufacturerData()) {
      line += "\"raw\":"; line += jsonString(hexEncode(device->getManufacturerData()));
    }
    line += "},\"service_data\":{";
    for (int i = 0; i < device->getServiceDataCount(); i++) {
      if (i) line += ',';
      String uuid = String(device->getServiceDataUUID(i).toString().c_str());
      line += jsonString(uuid); line += ':';
      line += jsonString(hexEncode(device->getServiceData(i)));
    }
    line += "}}";
    Serial.println(line);
    if (config.ssid.length() && config.serverHost.length()) enqueueForWiFi(line);
  }
};

void setup() {
  Serial.begin(115200);
  delay(1500);
  loadConfig();
  Serial.println("{\"type\":\"boot\",\"stage\":\"serial\"}");
  NimBLEDevice::init("home-monitor-proxy");
  scan = NimBLEDevice::getScan();
  scan->setScanCallbacks(new AdvertisedCallback(), false);
  scan->setActiveScan(true);
  scan->setInterval(160);
  scan->setWindow(80);
  Serial.print("{\"type\":\"ready\",\"firmware\":\"ble-proxy-wifi-0.2\",\"proxy_id\":");
  Serial.print(jsonString(config.proxyId));
  Serial.println("}");
}

void loop() {
  processSerialCommands();
  ensureWiFi();
  uploadQueued();
  scan->start(5, false);
  scan->clearResults();
  delay(100);
}
