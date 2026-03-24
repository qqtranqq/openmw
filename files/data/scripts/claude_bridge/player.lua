local bridge = require('openmw.bridge')
local selfModule = require('openmw.self')
local nearby = require('openmw.nearby')
local core = require('openmw.core')
local types = require('openmw.types')
local camera = require('openmw.camera')
local Actor = types.Actor
local Player = types.Player
local I = require('openmw.interfaces')

local json = require('scripts/claude_bridge/json')
local actions = require('scripts/claude_bridge/actions')

-- Configuration
local BRIDGE_PORT = 21003
local OBSERVATION_INTERVAL = 0.2  -- seconds between observations
local MAX_NEARBY = 20  -- max objects per category
local MAX_INVENTORY = 50  -- max inventory items to report

-- State
local initialized = false
local observationTimer = 0
local pendingResults = {}  -- results from global script events

-- Helper: safe get record name
local function getRecordName(obj)
    local ok, record = pcall(function() return obj.type.record(obj) end)
    if ok and record and record.name then
        return record.name
    end
    return obj.recordId or 'unknown'
end

-- Helper: distance from player
local function distanceFromPlayer(obj)
    local ppos = selfModule.object.position
    local opos = obj.position
    if not opos then return math.huge end
    return (opos - ppos):length()
end

-- Helper: sort objects by distance and cap count
local function nearestObjects(objectList, maxCount)
    local sorted = {}
    for _, obj in ipairs(objectList) do
        if obj.position then
            sorted[#sorted + 1] = obj
        end
    end
    table.sort(sorted, function(a, b) return distanceFromPlayer(a) < distanceFromPlayer(b) end)
    local result = {}
    for i = 1, math.min(#sorted, maxCount) do
        result[i] = sorted[i]
    end
    return result
end

-- Build player state observation
local function buildPlayerState()
    local obj = selfModule.object
    local pos = obj.position
    local rot = obj.rotation

    local health = Actor.stats.dynamic.health(obj)
    local magicka = Actor.stats.dynamic.magicka(obj)
    local fatigue = Actor.stats.dynamic.fatigue(obj)
    local level = Actor.stats.level(obj)

    local state = {
        position = {x = pos.x, y = pos.y, z = pos.z},
        cell = obj.cell and (obj.cell.name ~= '' and obj.cell.name or obj.cell.id) or 'unknown',
        health = {current = health.current, base = health.base},
        magicka = {current = magicka.current, base = magicka.base},
        fatigue = {current = fatigue.current, base = fatigue.base},
        level = level.current,
        stance = Actor.getStance(obj),
        onGround = Actor.isOnGround(obj),
        swimming = Actor.isSwimming(obj),
        speed = Actor.getCurrentSpeed(obj),
    }

    -- Camera direction
    local ok1, yaw = pcall(camera.getYaw)
    local ok2, pitch = pcall(camera.getPitch)
    if ok1 then state.cameraYaw = yaw end
    if ok2 then state.cameraPitch = pitch end

    return state
end

-- Build equipment info
local function buildEquipment()
    local eqp = Actor.getEquipment(selfModule.object)
    local result = {}
    for slot, item in pairs(eqp) do
        result[tostring(slot)] = {
            recordId = item.recordId,
            name = getRecordName(item),
        }
    end
    return result
end

-- Build inventory summary
local function buildInventory()
    local inv = Actor.inventory(selfModule.object):getAll()
    local items = {}
    local count = 0
    for _, item in ipairs(inv) do
        if count >= MAX_INVENTORY then break end
        count = count + 1
        items[count] = {
            recordId = item.recordId,
            name = getRecordName(item),
            count = item.count,
        }
    end
    return items
end

-- Build nearby actors info
local function buildNearbyActors()
    local actors = nearestObjects(nearby.actors, MAX_NEARBY)
    local result = {}
    for _, obj in ipairs(actors) do
        -- Skip player
        if obj.id ~= selfModule.object.id then
            local entry = {
                id = tostring(obj.id),
                name = getRecordName(obj),
                recordId = obj.recordId,
                position = {x = obj.position.x, y = obj.position.y, z = obj.position.z},
                distance = math.floor(distanceFromPlayer(obj)),
            }
            -- Health
            local ok, hp = pcall(function() return Actor.stats.dynamic.health(obj) end)
            if ok then
                entry.health = {current = hp.current, base = hp.base}
            end
            -- Hostile check
            local ok2, fight = pcall(function() return Actor.stats.ai.fight(obj).modified end)
            if ok2 then
                entry.hostile = fight > 70
            end
            -- Dead check
            local ok3, dead = pcall(function() return Actor.isDead(obj) end)
            if ok3 then
                entry.dead = dead
            end
            result[#result + 1] = entry
        end
    end
    return result
end

-- Build nearby doors info
local function buildNearbyDoors()
    local doors = nearestObjects(nearby.doors, MAX_NEARBY)
    local result = {}
    for _, obj in ipairs(doors) do
        local entry = {
            id = tostring(obj.id),
            name = getRecordName(obj),
            recordId = obj.recordId,
            position = {x = obj.position.x, y = obj.position.y, z = obj.position.z},
            distance = math.floor(distanceFromPlayer(obj)),
        }
        -- Check if it's a teleport door
        local ok, dest = pcall(function()
            local doorRecord = types.Door.record(obj)
            return doorRecord
        end)
        result[#result + 1] = entry
    end
    return result
end

-- Build nearby items info
local function buildNearbyItems()
    local items = nearestObjects(nearby.items, MAX_NEARBY)
    local result = {}
    for _, obj in ipairs(items) do
        result[#result + 1] = {
            id = tostring(obj.id),
            name = getRecordName(obj),
            recordId = obj.recordId,
            position = {x = obj.position.x, y = obj.position.y, z = obj.position.z},
            distance = math.floor(distanceFromPlayer(obj)),
            count = obj.count,
        }
    end
    return result
end

-- Build nearby containers info
local function buildNearbyContainers()
    local containers = nearestObjects(nearby.containers, MAX_NEARBY)
    local result = {}
    for _, obj in ipairs(containers) do
        result[#result + 1] = {
            id = tostring(obj.id),
            name = getRecordName(obj),
            recordId = obj.recordId,
            position = {x = obj.position.x, y = obj.position.y, z = obj.position.z},
            distance = math.floor(distanceFromPlayer(obj)),
        }
    end
    return result
end

-- Build nearby activators info
local function buildNearbyActivators()
    local activators = nearestObjects(nearby.activators, MAX_NEARBY)
    local result = {}
    for _, obj in ipairs(activators) do
        result[#result + 1] = {
            id = tostring(obj.id),
            name = getRecordName(obj),
            recordId = obj.recordId,
            position = {x = obj.position.x, y = obj.position.y, z = obj.position.z},
            distance = math.floor(distanceFromPlayer(obj)),
        }
    end
    return result
end

-- Build quest info
local function buildQuests()
    local result = {}
    local ok, quests = pcall(function() return Player.quests(selfModule.object) end)
    if ok and quests then
        for _, quest in ipairs(quests) do
            local entry = {
                id = quest.id,
                stage = quest.stage,
                finished = quest.finished,
            }
            result[#result + 1] = entry
        end
    end
    return result
end

-- Build full observation
local function buildObservation()
    return {
        type = 'observation',
        timestamp = core.getSimulationTime(),
        player = buildPlayerState(),
        equipment = buildEquipment(),
        inventory = buildInventory(),
        nearby = {
            actors = buildNearbyActors(),
            doors = buildNearbyDoors(),
            items = buildNearbyItems(),
            containers = buildNearbyContainers(),
            activators = buildNearbyActivators(),
        },
        quests = buildQuests(),
        currentAction = actions.getCurrentAction(),
    }
end

-- Send a message over the bridge
local function bridgeSend(msg)
    bridge.send(json.encode(msg))
end

-- Process incoming commands from the bridge
local function processIncoming()
    local messages = bridge.poll()
    for _, raw in ipairs(messages) do
        local ok, cmd = pcall(json.decode, raw)
        if ok and cmd then
            if cmd.type == 'action' then
                -- Handle global actions by forwarding to global script
                if cmd.action == 'teleport' then
                    core.sendGlobalEvent('ClaudeBridgeTeleport', {
                        id = cmd.id,
                        cell = cmd.params.cell,
                        position = cmd.params.position,
                        rotation = cmd.params.rotation,
                    })
                elseif cmd.action == 'create_object' then
                    core.sendGlobalEvent('ClaudeBridgeCreateObject', {
                        id = cmd.id,
                        recordId = cmd.params.recordId,
                        count = cmd.params.count,
                        intoInventory = cmd.params.intoInventory,
                    })
                elseif cmd.action == 'advance_time' then
                    core.sendGlobalEvent('ClaudeBridgeAdvanceTime', {
                        id = cmd.id,
                        hours = cmd.params.hours,
                    })
                elseif cmd.action == 'pause' then
                    core.sendGlobalEvent('ClaudeBridgePause', {
                        id = cmd.id,
                        unpause = cmd.params.unpause,
                    })
                elseif cmd.action == 'get_world_info' then
                    core.sendGlobalEvent('ClaudeBridgeGetWorldInfo', {id = cmd.id})
                else
                    -- Local player action
                    local result = actions.processCommand(cmd)
                    if result then
                        bridgeSend({type = 'action_result', id = result.id, success = result.success, message = result.message})
                    end
                end
            elseif cmd.type == 'ping' then
                bridgeSend({type = 'pong', id = cmd.id})
            end
        else
            print('Claude bridge: failed to parse message: ' .. raw)
        end
    end
end

-- Engine handlers
local function onFrame(dt)
    if not initialized then
        bridge.start(BRIDGE_PORT)
        initialized = true
        print('Claude bridge: listening on port ' .. BRIDGE_PORT)
    end

    if not bridge.isConnected() then
        return
    end

    -- Process incoming commands
    processIncoming()

    -- Update current action
    local completion = actions.update(dt)
    if completion then
        bridgeSend(completion)
    end

    -- Send observations periodically
    observationTimer = observationTimer + dt
    if observationTimer >= OBSERVATION_INTERVAL then
        observationTimer = 0
        local ok, obs = pcall(buildObservation)
        if ok then
            bridgeSend(obs)
        else
            print('Claude bridge: observation error: ' .. tostring(obs))
        end
    end

    -- Send any pending results from global events
    for i, result in ipairs(pendingResults) do
        bridgeSend(result)
    end
    pendingResults = {}
end

return {
    engineHandlers = {
        onFrame = onFrame,
    },
    eventHandlers = {
        -- Receive results from global script
        ClaudeBridgeActionResult = function(data)
            pendingResults[#pendingResults + 1] = {
                type = 'action_result',
                id = data.id,
                success = data.success,
                message = data.message,
            }
        end,
        ClaudeBridgeWorldInfo = function(data)
            pendingResults[#pendingResults + 1] = {
                type = 'world_info',
                id = data.id,
                info = data.info,
            }
        end,
    },
}
