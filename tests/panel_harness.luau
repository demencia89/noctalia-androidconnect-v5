-- Load this, panel.luau, then test_panel.luau in the same Luau VM.
commands, tree, frameTicks, testClock = {}, nil, false, 1000
testStatus = {
  serial = "TEST", adb = {{ serial = "TEST", name = "Test phone", state = "device" }},
  phase = "streaming", width = 344, height = 719,
  screen = { x = 14, y = 15, width = 310, height = 690, radius = 30 },
  phoneSize = 3, phoneSizeDirection = -1, audio = true,
}
ui = setmetatable({}, { __index = function(_, kind)
  assert(kind ~= "frame", "Removed ui.frame must not be used")
  return function(props, children) return { kind = kind, props = props or {}, children = children or {} } end
end })
panel = {
  setNeedsFrameTick = function(enabled) frameTicks = enabled end,
  render = function(value) tree = value end,
  close = function() didClose = true end,
}
noctalia = {
  tr = function(key) return key end,
  nowMs = function() return testClock end,
  getConfig = function(key) return if key == "preview_height" then 720 else nil end,
  clipboardText = function() return "clipboard text" end,
  state = {
    get = function() return testStatus end,
    set = function(key, value) if key == "command" then table.insert(commands, value) end end,
    watch = function(_, handler) statusChanged = handler end,
  },
}
