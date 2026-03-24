local world = require('openmw.world')
local core = require('openmw.core')
local types = require('openmw.types')

local function getPlayer()
    local players = world.players
    if #players > 0 then
        return players[1]
    end
    return nil
end

local function handleTeleport(data)
    local player = getPlayer()
    if not player then return end

    local cell = data.cell
    local position = data.position
    local rotation = data.rotation

    local opts = {}
    if rotation then
        opts.rotation = rotation
    end

    player:teleport(cell, position, opts)
    player:sendEvent('ClaudeBridgeActionResult', {
        id = data.id,
        success = true,
        message = 'Teleported to ' .. tostring(cell),
    })
end

local function handleCreateObject(data)
    local player = getPlayer()
    if not player then return end

    local recordId = data.recordId
    local count = data.count or 1

    local ok, result = pcall(function()
        local obj = world.createObject(recordId, count)
        if data.intoInventory then
            obj:moveInto(types.Actor.inventory(player))
        else
            -- Place near player
            obj:teleport(player.cell, player.position)
        end
        return obj
    end)

    player:sendEvent('ClaudeBridgeActionResult', {
        id = data.id,
        success = ok,
        message = ok and ('Created ' .. recordId) or ('Failed: ' .. tostring(result)),
    })
end

local function handleGetWorldInfo(data)
    local player = getPlayer()
    if not player then return end

    local info = {
        simulationTime = world.getSimulationTime(),
        gameTime = world.getGameTime(),
        isPaused = world.isWorldPaused(),
        activeActorCount = #world.activeActors,
    }

    player:sendEvent('ClaudeBridgeWorldInfo', {
        id = data.id,
        info = info,
    })
end

local function handleAdvanceTime(data)
    local player = getPlayer()
    if not player then return end

    local hours = data.hours or 1
    world.advanceTime(hours)

    if player then
        player:sendEvent('ClaudeBridgeActionResult', {
            id = data.id,
            success = true,
            message = 'Advanced time by ' .. hours .. ' hours',
        })
    end
end

local function handlePause(data)
    local player = getPlayer()
    if data.unpause then
        world.unpause('claudeBridge')
    else
        world.pause('claudeBridge')
    end
    if player then
        player:sendEvent('ClaudeBridgeActionResult', {
            id = data.id,
            success = true,
            message = data.unpause and 'Unpaused' or 'Paused',
        })
    end
end

return {
    eventHandlers = {
        ClaudeBridgeTeleport = handleTeleport,
        ClaudeBridgeCreateObject = handleCreateObject,
        ClaudeBridgeGetWorldInfo = handleGetWorldInfo,
        ClaudeBridgeAdvanceTime = handleAdvanceTime,
        ClaudeBridgePause = handlePause,
    },
}
