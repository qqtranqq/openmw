-- Minimal test script for the Claude bridge socket.
-- Starts the bridge on port 21003 and echoes any received messages back.

local bridge = require('openmw.bridge')

local BRIDGE_PORT = 21003
local initialized = false

local function onFrame(dt)
    if not initialized then
        bridge.start(BRIDGE_PORT)
        initialized = true
        print('Claude bridge test: listening on port ' .. BRIDGE_PORT)
    end

    local messages = bridge.poll()
    for _, msg in ipairs(messages) do
        print('Claude bridge test: received: ' .. msg)
        -- Echo it back with a pong
        local response = '{"type":"pong","echo":' .. msg .. '}'
        bridge.send(response)
    end
end

return {
    engineHandlers = {
        onFrame = onFrame,
    },
}
