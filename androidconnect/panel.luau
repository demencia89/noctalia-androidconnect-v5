--!nonstrict
local status = {}
local opened = false
local gesture = nil
local drawer = nil
local confirmUnpair = false
local text, filePath = "", ""
local host, pairPort, connectPort, code = "", "", "", ""
local inputRevision, commandId = 0, 0
local render
local typedText, typingTarget, typingSince = "", "", 0
local pendingScroll = nil
local lastScrollAt = 0
local pendingWindow = nil
local renameKey, renameText = "", ""
local sizeIndex, sizeDirection, sizeReady, sizeHovered = 1, 1, false, false
local SIZE_FACTORS = { 0.60, 0.75, 1.0 }
local SIZE_WIDTHS = { 560, 620, 680 }
local SIZE_NAMES = { "small", "medium", "large" }

local function tr(key) return noctalia.tr(key) end

local function label(props)
  props.fontFamily = "sans-serif"
  return ui.label(props)
end

local function command(action, values, phone)
  local request = values or {}
  request.action = action
  if phone then request.target = status.serial or "" end
  commandId += 1
  request.id = commandId
  noctalia.state.set("command", request)
end

local function syncInputTicks()
  panel.setNeedsFrameTick(typedText ~= "" or pendingScroll ~= nil)
end

local function flushTyping()
  if typedText ~= "" then
    command("text", { text = typedText, target = typingTarget })
    typedText = ""
  end
  syncInputTicks()
end

local function queueTyping(value)
  if #typedText + #value > 1024 then flushTyping() end
  if typedText == "" then
    typingTarget = status.serial
    typingSince = noctalia.nowMs()
  end
  typedText ..= value
  panel.setNeedsFrameTick(true)
end

local function flushScroll()
  if not pendingScroll then return end
  flushTyping()
  local scroll = pendingScroll
  pendingScroll = nil
  local horizontal = math.abs(scroll.dx) > math.abs(scroll.dy)
  local delta = if horizontal then scroll.dx else scroll.dy
  if delta ~= 0 then
    local distance = math.clamp(delta * 0.12, -0.45, 0.45)
    local x, y = math.clamp(scroll.x, 0.12, 0.88), math.clamp(scroll.y, 0.12, 0.88)
    local center = if horizontal then x else y
    local first = math.clamp(center + distance / 2, 0.12, 0.88)
    local last = math.clamp(center - distance / 2, 0.12, 0.88)
    command("gesture", {
      points = if horizontal then { first, y, last, y } else { x, first, x, last }, duration = 120,
    }, true)
    lastScrollAt = noctalia.nowMs()
  end
  syncInputTicks()
end

local function toggleDrawer(name)
  drawer = if drawer == name then nil else name
  confirmUnpair = false
  render()
end

local function caption(key)
  return label({ text = tr(key), fontSize = 12, color = "on_surface_variant", maxLines = 2 })
end

local function button(key, action, enabled)
  return ui.button({ text = tr(key), controlSize = "sm", enabled = enabled ~= false, onClick = action })
end

local function icon(key, glyph, action, enabled, active, size, glyphSize)
  size = size or 30
  local allowed = enabled ~= false
  return ui.column({
    key = key, width = size, height = size, minWidth = size, minHeight = size,
    align = "center", justify = "center", radius = size / 2,
    fill = if active then "primary/0.18" else "surface_variant/0.45",
    border = if active then "primary/0.65" else "outline/0.35", borderWidth = 1,
    opacity = if allowed then 1 else 0.35, tooltip = tr(key),
    onClick = if allowed then action else nil,
  }, {
    ui.glyph({ name = glyph, size = glyphSize or (if size > 36 then 17 else 15),
      color = if active then "primary" else "on_surface" }),
  })
end

local function selectedKde()
  for _, device in status.kde or {} do
    if device.id == status.selectedKde then return device end
  end
  return nil
end

local function selectedAdb()
  for _, device in status.adb or {} do
    if device.serial == status.serial then return device end
  end
  return nil
end

local function nameKey()
  local device = selectedKde()
  if device then return "kde:" .. device.id end
  return if selectedAdb() then "adb:" .. status.serial else ""
end

local function deviceName(key, original)
  return (status.names and status.names[key]) or original
end

local function brand(name)
  local lower = string.lower(name)
  local family = "android"
  if string.find(lower, "pixel") or string.find(lower, "google") then family = "google"
  elseif string.find(lower, "xiaomi") or string.find(lower, "redmi") or string.find(lower, "poco") then family = "xiaomi"
  elseif string.find(lower, "moto") then family = "motorola" end
  return "Assets/brand-badges/" .. family .. ".svg"
end

local function metric(glyph, title, value, color)
  return ui.row({ gap = 12, align = "start" }, {
    ui.glyph({ name = glyph, size = 23, width = 30, color = "on_surface_variant" }),
    ui.column({ gap = 2, align = "stretch" }, {
      caption(title),
      label({ text = value, fontSize = 22, fontWeight = "bold", color = color or "on_surface", maxLines = 1 }),
    }),
  })
end

local function deviceControls(width, device)
  local kdeNames, kdeIndex = { tr("devices.adb_only") }, 0
  for index, entry in status.kde or {} do
    table.insert(kdeNames, deviceName("kde:" .. entry.id, entry.name) .. (if entry.reachable then "" else " · " .. tr("status.disconnected")))
    if entry.id == status.selectedKde then kdeIndex = index end
  end
  local adbNames, adbEntries, adbIndex = { tr("devices.choose_adb") }, { "" }, 0
  for _, entry in status.adb or {} do
    if entry.state == "device" then
      table.insert(adbNames, deviceName("adb:" .. entry.serial, entry.name) .. " · " .. entry.serial)
      table.insert(adbEntries, entry.serial)
      if entry.serial == status.serial then adbIndex = #adbNames - 1 end
    end
  end
  local children = {
    caption("devices.kde"),
    ui.select({ key = "kde", width = width, options = kdeNames, selectedIndex = kdeIndex, onChange = function(value)
      local entry = status.kde and status.kde[tonumber(value)]
      gesture = nil
      confirmUnpair = false
      command("select", { kde = entry and entry.id or "" })
    end }),
    caption("devices.adb"),
    ui.select({ key = "adb", width = width, options = adbNames, selectedIndex = adbIndex, onChange = function(value)
      gesture = nil
      command("select", { serial = adbEntries[(tonumber(value) or 0) + 1] or "" })
    end }),
  }
  if device then
    table.insert(children, ui.row({ gap = 8 }, {
      button("actions.pair", function() command("pair", { kdeTarget = device.id }) end, not device.paired),
      button(if confirmUnpair then "actions.confirm_unpair" else "actions.unpair", function()
        if confirmUnpair then command("unpair", { kdeTarget = device.id }); confirmUnpair = false
        else confirmUnpair = true; render() end
      end, device.paired),
    }))
  end
  for _, entry in status.adb or {} do
    if entry.state ~= "device" then
      table.insert(children, label({ text = entry.serial .. ": " .. entry.state, color = "error", maxLines = 2 }))
    end
  end
  table.insert(children, button("actions.refresh", function() command("refresh") end))
  return children
end

local function wirelessControls(width)
  if status.qrActive then
    return {
      caption("wireless.scan_qr"),
      ui.image({ key = status.qrPath, path = status.qrPath, width = math.min(width, 240), height = 240 }),
      button("wireless.cancel_qr", function() command("cancelQr") end),
    }
  end
  return {
    label({ text = tr("wireless.instructions"), fontSize = 12, color = "on_surface_variant", maxLines = 5, maxWidth = width }),
    ui.input({ key = "host", value = host, controlSize = "sm", placeholder = tr("wireless.host"), onChange = function(value) host = value end }),
    ui.input({ key = "pair-port", value = pairPort, controlSize = "sm", placeholder = tr("wireless.pair_port"), onChange = function(value) pairPort = value end }),
    ui.input({ key = "code-" .. inputRevision, value = code, password = true, controlSize = "sm",
      placeholder = tr("wireless.code"), onChange = function(value) code = value end }),
    button("wireless.pair", function()
      command("pairAdb", { host = host, port = pairPort, code = code })
      code = ""; inputRevision += 1; render()
    end),
    ui.separator(),
    ui.input({ key = "connect-port", value = connectPort, controlSize = "sm",
      placeholder = tr("wireless.connect_port"), onChange = function(value) connectPort = value end }),
    ui.row({ gap = 8 }, {
      button("wireless.connect", function() command("connectAdb", { host = host, port = connectPort }) end),
      button("wireless.discover", function() command("autoConnect", { host = host }) end),
    }),
    button("wireless.qr", function() command("qr") end),
  }
end

local function drawerContent(width, device, active)
  if drawer == "rename" then
    return {
      caption("rename.local_only"),
      ui.input({ key = "rename-" .. inputRevision, value = renameText, controlSize = "sm",
        placeholder = tr("rename.name"), onChange = function(value) renameText = value end, onSubmit = "onRenameDevice" }),
      button("rename.save", onRenameDevice),
      button("rename.reset", function() renameText = ""; onRenameDevice() end),
    }
  end
  if drawer == "devices" then return deviceControls(width, device) end
  if drawer == "wireless" then return wirelessControls(width) end
  if drawer == "share" then
    return {
      caption("actions.file_path"),
      ui.input({ key = "file", value = filePath, controlSize = "sm", placeholder = tr("actions.file_path"),
        onChange = function(value) filePath = value end }),
      button("actions.send_file", function()
        command("share", { path = filePath, kdeTarget = device and device.id })
      end, device ~= nil and device.paired and device.reachable),
    }
  end
  if drawer == "text" then
    return {
      caption("actions.type_text"),
      ui.input({ key = "text-" .. inputRevision, value = text, controlSize = "sm",
        placeholder = tr("actions.type_text"), enabled = active, onSubmit = "onSendText",
        onChange = function(value) text = value end }),
      button("actions.send_text", onSendText, active),
    }
  end
  local children = {
    button("actions.devices", function() toggleDrawer("devices") end),
    button("actions.type_text", function() toggleDrawer("text") end, active),
    button("actions.settings", function() noctalia.openSettings() end),
    button("actions.refresh", function() command("refresh") end),
  }
  for name, available in status.tools or {} do
    table.insert(children, ui.row({ gap = 8 }, {
      ui.glyph({ name = if available then "check" else "x", size = 14, color = if available then "primary" else "error" }),
      label({ text = name, fontSize = 12, color = "on_surface_variant" }),
    }))
  end
  table.insert(children, button("actions.close", function() panel.close() end))
  return children
end

render = function()
  if not opened then return end
  local active = status.serial ~= nil and status.serial ~= ""
  local streaming = status.phase == "streaming"
  local mirroring = streaming or status.phase == "starting"
  local device, adb = selectedKde(), selectedAdb()
  local kdeReady = device ~= nil and device.paired and device.reachable
  local originalName = if device then device.name elseif adb then adb.name else tr("title")
  local identity = nameKey()
  local name = deviceName(identity, originalName)
  local phoneHeight = math.floor(math.clamp(noctalia.getConfig("preview_height") or 720, 240, 720) * SIZE_FACTORS[sizeIndex])
  local panelWidth = SIZE_WIDTHS[sizeIndex]
  local contentWidth = panelWidth - 28
  local aspect = if status.sourceWidth and status.sourceWidth > 0 then status.sourceWidth / status.sourceHeight else 538 / 1190
  local screenWidth = math.min(360, math.floor(phoneHeight * aspect))
  local phoneWidth = screenWidth + 4
  local navigationScale = math.max(0.75, SIZE_FACTORS[sizeIndex])
  local navigationSize = math.round(42 * navigationScale)
  local navigationGlyphSize = math.round(17 * navigationScale)
  local function navigationButton(key, glyph, androidKey)
    return ui.column({ key = key .. "-slot", width = phoneWidth * 0.32, height = navigationSize,
      align = "center", justify = "center",
    }, {
      icon(key, glyph, function() command("key", { key = androidKey }, true) end,
        active, false, navigationSize, navigationGlyphSize),
    })
  end
  local corner = math.max(12, phoneHeight * 44.3 / 1190)
  local rightWidth = contentWidth - phoneWidth - 18
  local toolsWidth = math.min(rightWidth, math.round(300 * SIZE_FACTORS[sizeIndex]))
  local frame = ui.column({ key = "phone-rim", width = phoneWidth, padding = 2,
    radius = corner + 2, fill = "#08090c", border = "outline/0.65", borderWidth = 1,
  }, {
    ui.frame({ key = "phone", path = if mirroring then status.socket or "" else "",
      width = screenWidth, height = phoneHeight, radius = corner,
      onPointer = "onFramePointer", onKey = "onFrameKey" }),
  })

  local info
  if drawer then
    local heading = if drawer == "wireless" then "actions.wireless" elseif drawer == "share" then "actions.send_file"
      elseif drawer == "devices" then "actions.devices" elseif drawer == "text" then "actions.type_text"
      elseif drawer == "rename" then "actions.rename_device" else "actions.diagnostics"
    local children = {
      ui.row({ align = "center", gap = 8 }, {
        label({ text = tr(heading), fontSize = 15, fontWeight = "bold", flexGrow = 1 }),
        icon("actions.back_to_status", "x", function() drawer = nil; render() end),
      }),
    }
    for _, child in drawerContent(rightWidth - 24, device, active) do table.insert(children, child) end
    info = ui.column({ key = "drawer", width = rightWidth, padding = 12, gap = 10, radius = 18,
      fill = "surface_variant/0.4", border = "outline/0.2", borderWidth = 1 }, children)
  else
    local telemetry = if kdeReady then device else (status.phone or {})
    local battery = telemetry.battery or -1
    local network = if kdeReady and device.network and device.network ~= "" then device.network
      elseif adb then (if adb.usb then "USB" else "Wi-Fi") else tr("device.unknown")
    local signal = telemetry.signal or -1
    local strength = if signal < 0 then "unknown" elseif signal == 0 then "none" elseif signal == 1 then "weak"
      elseif signal == 2 then "fair" elseif signal == 3 then "good" else "excellent"
    local children = {
      metric(if telemetry.charging then "battery-charging" else "battery", "device.battery",
        if battery < 0 then tr("device.unknown") else tostring(battery) .. "%",
        if battery >= 0 and battery < 20 then "error" elseif battery > 95 then "#75c98a" else "on_surface"),
      metric(if network == "USB" then "usb" else "antenna-bars-5", "device.network", network),
      metric("antenna-bars-" .. tostring(math.clamp(signal + 1, 1, 5)), "device.signal", tr("signal." .. strength)),
    }
    if telemetry.notifications ~= nil then
      table.insert(children, metric("bell", "device.notifications", tostring(telemetry.notifications)))
    end
    table.insert(children, ui.column({ key = "phone-tools-slot", width = rightWidth, align = "start" }, {
      ui.row({ key = "phone-tools", width = toolsWidth, height = 42, padding = 6, gap = 0,
        justify = "space_between", align = "center", radius = 22, fill = "surface_variant/0.5",
      }, {
        icon("actions.screenshot", "camera", function() command("screenshot", nil, true) end, active),
        icon(if status.recording then "actions.stop_recording" else "actions.record", "video",
          function() command("record", nil, true) end, active, status.recording),
        icon("actions.keep_awake", "moon", function() command("keepAwake", nil, true) end, mirroring, status.keepAwake),
        icon("actions.dim", "sun", function() command("dim", nil, true) end, mirroring, status.dim),
      }),
    }))
    if status.error and status.error ~= "" then
      table.insert(children, label({ text = status.error, fontSize = 12, maxLines = 7,
        maxWidth = rightWidth, color = "error" }))
    end
    info = ui.column({ key = "status", width = rightWidth, gap = 20, paddingV = 12, align = "stretch" }, children)
  end

  local phoneColumnHeight = phoneHeight + 4 + 10 + navigationSize
  local minimumBodyHeight = if drawer then 504 else 380
  -- Match the host's 14px inset on every side, plus header and body spacing.
  panel.setSize(panelWidth, 28 + 54 + 14 + math.max(phoneColumnHeight, minimumBodyHeight))
  panel.render(ui.column({ gap = 14, align = "stretch" }, {
    ui.row({ key = "header", height = 54, gap = 7, paddingH = 8, paddingV = 8, align = "center",
      fill = "surface_variant/0.5", radius = 25 }, {
      ui.column({ key = "phone-size", width = 30, height = 30, align = "center", justify = "center",
        onClick = "onCyclePhoneSize", onHover = "onSizeHover",
        tooltip = tr("size.tooltip") .. " " .. tr("size." .. SIZE_NAMES[sizeIndex])
          .. " (" .. tostring(SIZE_FACTORS[sizeIndex] * 100) .. "%)",
      }, {
        if sizeHovered then ui.glyph({ name = "resize", size = 21, color = "on_surface" })
          else ui.image({ path = brand(originalName), width = 30, height = 30 }),
      }),
      ui.column({ key = "device-name", flexGrow = 1, minWidth = 0, height = 30, justify = "center",
        tooltip = tr("actions.rename_device"), onClick = if identity ~= "" then function()
          renameKey, renameText = identity, name
          inputRevision += 1
          drawer = "rename"
          render()
        end else nil,
      }, {
        label({ text = name, fontSize = 25, fontWeight = "bold", maxLines = 1 }),
      }),
      icon("actions.devices", "devices", function() toggleDrawer("devices") end, true, drawer == "devices"),
      icon("actions.diagnostics", "info-circle", function() toggleDrawer("info") end, true, drawer == "info"),
      icon("actions.open_window", "maximize", onOpenWindow, active and not status.busy),
      icon("actions.audio", if status.audio then "volume" else "volume-off",
        function() command("audio", nil, true) end, active, status.audio),
      icon("actions.wireless", "wifi", function() toggleDrawer("wireless") end, true, drawer == "wireless"),
      icon("actions.browse", "device-mobile-search", function() command("browse", { kdeTarget = device and device.id }) end, kdeReady),
      icon("actions.send_file", "device-mobile-share", function() toggleDrawer("share") end, kdeReady, drawer == "share"),
      icon("actions.ring", "radar", function() command("ring", { kdeTarget = device and device.id }) end, kdeReady),
    }),
    ui.row({ key = "body", gap = 18, align = "start" }, {
      ui.column({ key = "phone-column", width = phoneWidth, gap = 10, align = "center" }, {
        frame,
        ui.row({ width = phoneWidth, paddingH = phoneWidth * 0.02, gap = 0 }, {
          navigationButton("navigation.back", "arrow-back", 4),
          navigationButton("navigation.home", "home", 3),
          navigationButton("navigation.recents", "layout-grid", 187),
        }),
      }),
      info,
    }),
  }))
end

function onRenameDevice()
  if not opened or renameKey == "" or renameKey ~= nameKey() then return end
  command("rename", { nameKey = renameKey, name = renameText })
  renameKey, renameText, drawer = "", "", nil
  render()
end

function onOpenWindow()
  if not opened or not status.serial or status.serial == "" or status.busy then return end
  flushTyping()
  gesture, pendingScroll = nil, nil
  syncInputTicks()
  pendingWindow = tostring(noctalia.nowMs()) .. "-" .. tostring(commandId + 1)
  command("window", { token = pendingWindow }, true)
end

function onCyclePhoneSize()
  if not opened then return end
  flushTyping()
  gesture, pendingScroll = nil, nil
  if sizeIndex == 3 then sizeDirection = -1 elseif sizeIndex == 1 then sizeDirection = 1 end
  sizeIndex += sizeDirection
  if sizeIndex == 3 then sizeDirection = -1 elseif sizeIndex == 1 then sizeDirection = 1 end
  sizeReady = true
  command("size", { preset = sizeIndex, direction = sizeDirection })
  syncInputTicks()
  render()
end

function onSizeHover(entered)
  sizeHovered = entered == "true"
  render()
end

function onOpen()
  opened = true
  pendingWindow = nil
  gesture = nil
  drawer = nil
  status = noctalia.state.get("status") or {}
  sizeIndex = status.phoneSize or 1
  sizeDirection = status.phoneSizeDirection or 1
  sizeReady = status.phoneSize ~= nil
  sizeHovered = false
  command("open")
  render()
end

function onClose()
  opened = false
  pendingWindow = nil
  renameKey, renameText = "", ""
  gesture = nil
  code, text = "", ""
  typedText = ""
  pendingScroll = nil
  panel.setNeedsFrameTick(false)
  inputRevision += 1
  command("close")
end

function onExit()
  if opened then onClose() end
end

function onFramePointer(kind, payload)
  if not opened then return end
  if kind == "cancel" then gesture = nil; return end
  local position = noctalia.json.decode(payload)
  if type(position) ~= "table" then return end
  if kind == "scroll" then
    if status.phase ~= "streaming" or gesture then return end
    flushTyping()
    local previous = pendingScroll
    pendingScroll = {
      x = position.x, y = position.y,
      dx = math.clamp((previous and previous.dx or 0) + (position.dx or 0), -4, 4),
      dy = math.clamp((previous and previous.dy or 0) + (position.dy or 0), -4, 4),
    }
    syncInputTicks()
  elseif kind == "press" then
    flushScroll()
    flushTyping()
    gesture = nil
    if position.button == "right" then
      command("key", { key = 4 }, true)
    elseif position.button == "middle" then
      command("key", { key = 3 }, true)
    else
      gesture = { x = position.x, y = position.y, time = noctalia.nowMs(), serial = status.serial }
    end
  elseif kind == "release" and gesture then
    local first = gesture
    gesture = nil
    if first.serial ~= status.serial then return end
    command("gesture", { points = { first.x, first.y, position.x, position.y },
      duration = math.clamp(noctalia.nowMs() - first.time, 50, 3000) }, true)
  end
end

function onSendText()
  if text == "" then return end
  command("text", { text = text }, true)
  text = ""; inputRevision += 1; render()
end

function onFrameKey(kind, payload)
  if not opened or status.phase ~= "streaming" or kind ~= "press" then return end
  local key = noctalia.json.decode(payload)
  if type(key) ~= "table" or key.preedit then return end
  flushScroll()
  -- KeyMod: Shift=1, Ctrl=2, Alt=4, Super=8. Text already reflects
  -- the keyboard layout, Shift and Caps Lock; do not reconstruct it from key names.
  if bit32.band(key.modifiers, 14) ~= 0 then
    if key.modifiers ~= 2 or (key.sym ~= 118 and key.sym ~= 86) then return end
    flushTyping()
    local clipboard = noctalia.clipboardText()
    if clipboard and clipboard ~= "" then command("text", { text = clipboard }, true) end
    return
  end
  local keys = {
    [0xff08] = 67, [0xffff] = 112, [0xff0d] = 66, [0xff8d] = 66,
    [0xff51] = 21, [0xff52] = 19, [0xff53] = 22, [0xff54] = 20,
    [0xff50] = 122, [0xff57] = 123, [0xff55] = 92, [0xff56] = 93,
  }
  if keys[key.sym] then
    flushTyping()
    command("key", { key = keys[key.sym] }, true)
  elseif key.utf32 and key.utf32 >= 32 and key.utf32 <= 0x10ffff then
    queueTyping(utf8.char(key.utf32))
  end
end

function onFrameTick()
  if typedText ~= "" and noctalia.nowMs() - typingSince >= 40 then flushTyping() end
  if pendingScroll and not status.busy and noctalia.nowMs() - lastScrollAt >= 180 then flushScroll() end
end

noctalia.state.watch("status", function(value)
  if type(value) ~= "table" then return end
  if value.serial ~= status.serial then pendingWindow = nil end
  if opened and pendingWindow and value.windowRequest == pendingWindow then
    pendingWindow = nil
    status = value
    panel.close()
    return
  end
  if not sizeReady and value.phoneSize then
    sizeIndex, sizeDirection = value.phoneSize, value.phoneSizeDirection or 1
    sizeReady = true
  end
  if value.serial ~= status.serial or value.phase ~= "streaming" then
    gesture = nil
    typedText = ""
    pendingScroll = nil
    panel.setNeedsFrameTick(false)
  end
  status = value
  if drawer == "rename" and renameKey ~= nameKey() then
    renameKey, renameText, drawer = "", "", nil
  end
  render()
end)
