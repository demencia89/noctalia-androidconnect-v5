local function find(node, key)
  if node.props.key == key then return node end
  for _, child in node.children do
    local result = find(child, key)
    if result then return result end
  end
end
local function near(a, b) assert(math.abs(a - b) < 0.00001, tostring(a) .. " ~= " .. tostring(b)) end
local function point(x, y)
  return tostring((14 + 310 * x) / 344), tostring((15 + 690 * y) / 719)
end
local function checkColors(node)
  for _, property in { "fill", "border", "color" } do
    local value = node.props[property]
    assert(not value or not string.find(value, "#", 1, true), "Colors must track the theme")
  end
  for _, child in node.children do checkColors(child) end
end

for size, height in { 432, 540, 720 } do
  testStatus.phoneSize = size
  onOpen()
  local phone = find(tree, "phone")
  assert(phone.kind == "pixelStream" and phone.props.height == height)
  assert(phone.props.onPointerDown == "onPixelDown" and phone.props.onKeyEvent == "onPixelKey")
  assert(phone.props.onPointer == nil and phone.props.onKey == nil)
  for _, phase in { "idle", "starting", "error" } do
    local idle = table.clone(testStatus)
    idle.phase = phase
    statusChanged(idle)
    local placeholder = find(tree, "phone-placeholder")
    assert(placeholder.props.path == "Assets/phone-idle.svg" and placeholder.props.height == height)
  end
  statusChanged(testStatus)
  local count = #commands
  onPixelDown("0", "0"); onPixelUp("0", "0")
  onPixelBack("0", "0"); onPixelHome("0", "0")
  onPixelMove("0", "0"); onPixelScroll("0", "1")
  assert(#commands == count and not frameTicks, "Bezel input must be ignored")
  onPixelDown(point(0.25, 0.3)); onPixelUp(point(0.75, 0.7))
  local points = commands[#commands].points
  near(points[1], 0.25); near(points[2], 0.3); near(points[3], 0.75); near(points[4], 0.7)
  onPixelDown(point(0.5, 0.5)); onPixelUp("0", "1")
  near(commands[#commands].points[3], 0); near(commands[#commands].points[4], 1)
  onPixelBack(point(0.5, 0.5)); assert(commands[#commands].key == 4)
  onPixelHome(point(0.5, 0.5)); assert(commands[#commands].key == 3)
  count = #commands
  onPixelDown(point(0.5, 0.5)); onPixelCancel(); onPixelUp(point(0.7, 0.7))
  assert(#commands == count, "Canceled drag must not reach Android")
  onPixelScroll("0", "1"); assert(not frameTicks, "Canceled/stale pointer position must not scroll")
  onPixelMove(point(0.5, 0.5)); onPixelScroll("0", "1")
  testClock += 200; onFrameTick()
  assert(commands[#commands].action == "gesture")
  assert(commands[#commands].points[2] > commands[#commands].points[4])
  for _, row in { find(tree, "quick-actions").children[2], find(tree, "quick-actions").children[3] } do
    for _, tile in row.children do
      assert(tile.props.width == nil and tile.props.flexGrow == 1 and tile.props.minWidth == 0)
    end
  end
  onControlHover("true", "actions.screenshot")
  assert(find(tree, "actions.screenshot").props.fill == "primary/0.08")
  onControlHover("false", "actions.screenshot")
  checkColors(tree)
end

onPixelText("a"); onPixelText("B")
onPixelKey("Return", "true")
assert(commands[#commands - 1].text == "aB" and commands[#commands].key == 66)
local count = #commands
onPixelKey("Return", "false"); onPixelKey("Ctrl+c", "true")
assert(#commands == count, "Key releases and unsupported chords must be ignored")
onPixelKey("Shift+Left", "true"); assert(commands[#commands].key == 21)
onPixelKey("Ctrl+v", "true"); assert(commands[#commands].text == "clipboard text")
onPixelText("repeat"); testClock += 50; onFrameTick()
assert(commands[#commands].text == "repeat" and not frameTicks)

onPixelMove(point(0.5, 0.5)); onPixelScroll("0", "1")
local busy = table.clone(testStatus); busy.busy = true; statusChanged(busy)
count = #commands; testClock += 200; onFrameTick(); assert(#commands == count)
statusChanged(testStatus); onFrameTick(); assert(#commands == count + 1)

onPixelDown(point(0.5, 0.5)); onCyclePhoneSize()
count = #commands; onPixelUp(point(0.7, 0.7)); assert(#commands == count)
testStatus.phoneSize = nil
onOpen(); assert(find(tree, "phone").props.height == 720, "Large remains the initial default")
local saved = table.clone(testStatus); saved.phoneSize = 1; statusChanged(saved)
assert(find(tree, "phone").props.height == 432, "Late saved size must win")

testStatus.phoneSize, testStatus.phoneSizeDirection = 3, -1
onOpen()
for _, expected in { 540, 432, 540, 720 } do
  onCyclePhoneSize(); assert(find(tree, "phone").props.height == expected)
end
find(tree, "actions.more").props.onClick()
for _, key in { "actions.devices", "actions.browse", "actions.send_file", "actions.ring", "actions.type_text", "actions.diagnostics" } do
  assert(find(tree, key), "More must retain " .. key)
end
find(tree, "actions.devices").props.onClick()
local multiple = table.clone(testStatus)
multiple.adb = {{serial = "TEST", name = "One", state = "device"}, {serial = "SECOND", name = "Two", state = "device"}}
statusChanged(multiple); assert(find(tree, "adb"))
find(tree, "actions.wireless").props.onClick()
assert(commands[#commands].action == "wirelessOpen")

onClose()
count = #commands
onPixelText("closed"); onPixelKey("Return", "true")
onPixelDown(point(0.5, 0.5)); onPixelUp(point(0.7, 0.7))
assert(#commands == count and not frameTicks)
print("Panel: upstream callbacks, all sizes, bezel mapping, text/keys, scroll, cancellation, saved sizes and drawers OK")
