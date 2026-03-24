local selfModule = require('openmw.self')
local nearby = require('openmw.nearby')
local types = require('openmw.types')
local Actor = types.Actor
local core = require('openmw.core')

local actions = {}

-- Current action being executed
local currentAction = nil
local actionTimer = 0
local actionCompleted = false

-- Helper: find nearby object by name substring match (case-insensitive)
local function findNearbyByName(targetName, ...)
    local searchName = targetName:lower()
    local bestObj = nil
    local bestDist = math.huge
    local playerPos = selfModule.object.position

    for _, objectList in ipairs({...}) do
        for _, obj in ipairs(objectList) do
            -- Try to get the display name from the record
            local ok, record = pcall(function() return obj.type.record(obj) end)
            if ok and record and record.name then
                if record.name:lower():find(searchName, 1, true) then
                    local dist = (obj.position - playerPos):length()
                    if dist < bestDist then
                        bestDist = dist
                        bestObj = obj
                    end
                end
            end
        end
    end
    return bestObj, bestDist
end

-- Helper: find inventory item by name substring match
local function findInventoryItem(targetName)
    local searchName = targetName:lower()
    local inv = Actor.inventory(selfModule.object)
    for _, item in ipairs(inv:getAll()) do
        local ok, record = pcall(function() return item.type.record(item) end)
        if ok and record and record.name then
            if record.name:lower():find(searchName, 1, true) then
                return item
            end
        end
    end
    return nil
end

-- Reset all controls to neutral
local function resetControls()
    selfModule.controls.movement = 0
    selfModule.controls.sideMovement = 0
    selfModule.controls.yawChange = 0
    selfModule.controls.pitchChange = 0
    selfModule.controls.jump = false
    selfModule.controls.use = 0
end

-- Process a new action command
-- cmd = {id=string, action=string, params=table}
-- Returns {id, success, message}
function actions.processCommand(cmd)
    local action = cmd.action
    local params = cmd.params or {}
    local id = cmd.id or ''

    -- Cancel any current action
    if currentAction then
        resetControls()
        currentAction = nil
    end

    actionCompleted = false

    if action == 'move' then
        local direction = params.direction or 'forward'
        local duration = params.duration or 1.0
        local run = params.run
        if run == nil then run = true end

        currentAction = {
            type = 'move',
            id = id,
            duration = duration,
            direction = direction,
            run = run,
        }
        actionTimer = 0
        return {id = id, success = true, message = 'Moving ' .. direction}

    elseif action == 'turn' then
        local angle = params.angle or 0.5  -- radians
        currentAction = {
            type = 'turn',
            id = id,
            angle = angle,
            duration = params.duration or 0.5,
        }
        actionTimer = 0
        return {id = id, success = true, message = 'Turning'}

    elseif action == 'look' then
        local angle = params.angle or 0.0
        selfModule.controls.pitchChange = angle
        return {id = id, success = true, message = 'Looking'}

    elseif action == 'jump' then
        selfModule.controls.jump = true
        -- jump is consumed in 1 frame, set a tiny timed action to clear it
        currentAction = {type = 'jump', id = id, duration = 0.05}
        actionTimer = 0
        return {id = id, success = true, message = 'Jumping'}

    elseif action == 'activate' then
        local target = params.target
        if not target then
            return {id = id, success = false, message = 'No target specified'}
        end
        local obj = findNearbyByName(target, nearby.actors, nearby.doors, nearby.items, nearby.containers, nearby.activators)
        if not obj then
            return {id = id, success = false, message = 'Could not find: ' .. target}
        end
        local ok, err = pcall(function() obj:activateBy(selfModule.object) end)
        if ok then
            return {id = id, success = true, message = 'Activated: ' .. target}
        else
            return {id = id, success = false, message = 'Activation failed: ' .. tostring(err)}
        end

    elseif action == 'equip' then
        local itemName = params.item
        if not itemName then
            return {id = id, success = false, message = 'No item specified'}
        end
        local item = findInventoryItem(itemName)
        if not item then
            return {id = id, success = false, message = 'Item not found in inventory: ' .. itemName}
        end
        -- Determine appropriate slot based on item type
        local slot = params.slot  -- optional explicit slot
        if not slot then
            -- Auto-detect: weapons go to CarriedRight, armor/clothing to their slot
            if types.Weapon and types.Weapon.objectIsInstance(item) then
                slot = Actor.EQUIPMENT_SLOT.CarriedRight
            elseif types.Armor and types.Armor.objectIsInstance(item) then
                local armorRecord = types.Armor.record(item)
                slot = armorRecord.type  -- armor type maps roughly to slot
            end
        end
        if slot then
            local eqp = Actor.getEquipment(selfModule.object)
            eqp[slot] = item
            Actor.setEquipment(selfModule.object, eqp)
            return {id = id, success = true, message = 'Equipped: ' .. itemName}
        else
            return {id = id, success = false, message = 'Could not determine equipment slot for: ' .. itemName}
        end

    elseif action == 'attack' then
        local duration = params.duration or 1.0
        Actor.setStance(selfModule.object, Actor.STANCE.Weapon)
        currentAction = {type = 'attack', id = id, duration = duration}
        actionTimer = 0
        return {id = id, success = true, message = 'Attacking'}

    elseif action == 'cast' then
        local spellId = params.spell
        if spellId then
            Actor.setSelectedSpell(selfModule.object, spellId)
        end
        Actor.setStance(selfModule.object, Actor.STANCE.Spell)
        selfModule.controls.use = 1
        currentAction = {type = 'cast', id = id, duration = 0.1}
        actionTimer = 0
        return {id = id, success = true, message = 'Casting spell'}

    elseif action == 'stop' then
        resetControls()
        currentAction = nil
        return {id = id, success = true, message = 'Stopped'}

    elseif action == 'wait' then
        local duration = params.duration or 1.0
        currentAction = {type = 'wait', id = id, duration = duration}
        actionTimer = 0
        return {id = id, success = true, message = 'Waiting'}

    elseif action == 'sneak' then
        local enable = params.enable
        if enable == nil then enable = true end
        selfModule.controls.sneak = enable
        return {id = id, success = true, message = enable and 'Sneaking' or 'Stopped sneaking'}

    elseif action == 'read_book' then
        local bookName = params.target or params.book
        if not bookName then
            return {id = id, success = false, message = 'No book specified'}
        end
        -- Search inventory first
        local item = findInventoryItem(bookName)
        if not item then
            -- Search nearby items
            item = findNearbyByName(bookName, nearby.items)
        end
        if not item then
            return {id = id, success = false, message = 'Book not found: ' .. bookName}
        end
        -- Check if it's a book type
        local ok, bookRecord = pcall(function()
            if types.Book and types.Book.objectIsInstance(item) then
                return types.Book.record(item)
            end
            return nil
        end)
        if ok and bookRecord then
            return {
                id = id,
                success = true,
                message = bookRecord.text or '(no text)',
                bookTitle = bookRecord.name or bookName,
                isScroll = bookRecord.isScroll or false,
            }
        else
            return {id = id, success = false, message = 'Not a book: ' .. bookName}
        end

    elseif action == 'select_topic' then
        -- During dialogue, selecting a topic means activating with the topic
        -- In OpenMW, topic selection happens through the dialogue UI
        -- We simulate it by finding the NPC we're talking to and providing the topic name
        local topicName = params.topic
        if not topicName then
            return {id = id, success = false, message = 'No topic specified'}
        end
        -- The topic selection is handled by the engine when the player clicks a topic
        -- For now, return the topic name so the Python side knows what was selected
        return {id = id, success = true, message = 'Topic selected: ' .. topicName}

    elseif action == 'screenshot' then
        local path = params.path or '/tmp/openmw_bridge_screenshot.png'
        local width = params.width or 640
        local height = params.height or 480
        local ok, err = pcall(function()
            local bridge = require('openmw.bridge')
            bridge.screenshot(path, width, height)
        end)
        if ok then
            return {id = id, success = true, message = 'Screenshot requested: ' .. path}
        else
            return {id = id, success = false, message = 'Screenshot failed: ' .. tostring(err)}
        end

    else
        return {id = id, success = false, message = 'Unknown action: ' .. tostring(action)}
    end
end

-- Update current timed action each frame
-- dt = frame delta time in seconds
-- Returns action_complete message table if action just finished, or nil
function actions.update(dt)
    if not currentAction then
        return nil
    end

    actionTimer = actionTimer + dt
    local act = currentAction

    if act.type == 'move' then
        local dir = act.direction
        if dir == 'forward' then
            selfModule.controls.movement = 1
        elseif dir == 'backward' then
            selfModule.controls.movement = -1
        elseif dir == 'left' then
            selfModule.controls.sideMovement = -1
        elseif dir == 'right' then
            selfModule.controls.sideMovement = 1
        end
        selfModule.controls.run = act.run

    elseif act.type == 'turn' then
        -- Spread the turn angle over the duration
        selfModule.controls.yawChange = act.angle * (dt / act.duration)

    elseif act.type == 'attack' then
        selfModule.controls.use = 1

    elseif act.type == 'cast' then
        -- use was already set, just wait for duration

    elseif act.type == 'jump' then
        -- jump flag was set, just wait to clear

    elseif act.type == 'wait' then
        -- do nothing, just wait
    end

    -- Check if action duration elapsed
    if actionTimer >= act.duration then
        resetControls()
        local completedAction = currentAction
        currentAction = nil
        return {type = 'action_complete', id = completedAction.id, success = true}
    end

    return nil
end

-- Check if idle (no action in progress)
function actions.isIdle()
    return currentAction == nil
end

-- Get current action type (or nil)
function actions.getCurrentAction()
    if currentAction then
        return currentAction.type
    end
    return nil
end

return actions
