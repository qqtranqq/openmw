local selfModule = require('openmw.self')
local nearby = require('openmw.nearby')
local types = require('openmw.types')
local Actor = types.Actor
local core = require('openmw.core')
local I = require('openmw.interfaces')
local camera = require('openmw.camera')
local util = require('openmw.util')

local actions = {}

-- Helper: point the character and camera directly at a target object
local function faceTarget(obj, dt)
    if not obj or not obj.position then return end
    local ppos = selfModule.object.position
    local opos = obj.position
    local dx = opos.x - ppos.x
    local dy = opos.y - ppos.y
    local dz = opos.z - ppos.z
    local horizDist = math.sqrt(dx * dx + dy * dy)

    -- Calculate target yaw in world coordinates
    local targetYaw = math.atan2(dx, dy)

    -- Turn the character toward target using controls
    local rot = selfModule.object.rotation
    local forward = rot:apply(util.vector3(0, 1, 0))
    local currentYaw = math.atan2(forward.x, forward.y)
    local yawDiff = targetYaw - currentYaw
    while yawDiff > math.pi do yawDiff = yawDiff - 2 * math.pi end
    while yawDiff < -math.pi do yawDiff = yawDiff + 2 * math.pi end

    -- Snap character rotation via large yawChange
    selfModule.controls.yawChange = yawDiff

    -- Set camera to face the same direction (yaw 0 = same as character facing)
    camera.setYaw(0)

    -- Pitch: look up/down at the target
    if horizDist > 1 then
        local targetPitch = math.atan2(dz, horizDist)
        camera.setPitch(targetPitch)
    end
end

-- Current action being executed
local currentAction = nil
local actionTimer = 0
local actionCompleted = false

-- Helper: find nearby object by name substring match (case-insensitive)
-- Also matches door destination cell names (e.g. "South Wall Cornerclub" matches a door leading there)
local function findNearbyByName(targetName, ...)
    -- Clean up search name: strip "(door)" suffix and common prefixes
    local searchName = targetName:lower()
    searchName = searchName:gsub('%s*%(door%)%s*$', '')  -- strip " (door)" suffix
    searchName = searchName:gsub('^.+,%s*', '')           -- strip "Balmora, " cell prefix
    local bestObj = nil
    local bestDist = math.huge
    local playerPos = selfModule.object.position

    for _, objectList in ipairs({...}) do
        for _, obj in pairs(objectList) do
            local matched = false
            -- Try to get the display name from the record
            local ok, record = pcall(function() return obj.type.record(obj) end)
            if ok and record and record.name then
                if record.name:lower():find(searchName, 1, true) then
                    matched = true
                end
            end
            -- For doors, also match against destination cell name
            if not matched and types.Door and types.Door.objectIsInstance and pcall(function() return types.Door.objectIsInstance(obj) end) and types.Door.objectIsInstance(obj) then
                local ok2, isTeleport = pcall(function() return types.Door.isTeleport(obj) end)
                if ok2 and isTeleport then
                    local ok3, destCell = pcall(function() return types.Door.destCell(obj) end)
                    if ok3 and destCell then
                        local destName = destCell.name or ''
                        -- Also try without cell prefix (e.g. "Balmora, X" -> "X")
                        local cleanDest = destName:match(',%s*(.+)') or destName
                        if destName:lower():find(searchName, 1, true) or cleanDest:lower():find(searchName, 1, true) then
                            matched = true
                        end
                    end
                end
            end
            if matched and obj.position then
                local dist = (obj.position - playerPos):length()
                if dist < bestDist then
                    bestDist = dist
                    bestObj = obj
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
    -- Restore vanilla controls
    pcall(function() I.Controls.overrideCombatControls(false) end)
    pcall(function() I.Controls.overrideUiControls(false) end)
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

    -- Auto-close dialogue if open and a non-dialogue action is requested
    -- This prevents getting stuck when accidentally opening dialogue during navigation
    local movementActions = {move=true, turn=true, navigate_to=true, navigate_to_npc=true,
        approach=true, attack=true, stop=true, jump=true, rest=true}
    if movementActions[action] then
        local bridgeModule = require('openmw.bridge')
        local okDlg, isOpen = pcall(function() return bridgeModule.isDialogueOpen() end)
        if okDlg and isOpen then
            pcall(function() bridgeModule.closeDialogue() end)
            print('Claude bridge: auto-closed unexpected dialogue before ' .. action)
        end
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

    elseif action == 'open_and_enter' then
        -- Activate a door/gate and walk through it (for interior physical doors)
        local target = params.target or 'Door'
        local obj = findNearbyByName(target, nearby.doors, nearby.activators, nearby.items, nearby.containers)
        if not obj then
            return {id = id, success = false, message = 'Could not find door: ' .. target}
        end
        -- Face the door first
        if obj.position then
            faceTarget(obj, 0)
        end
        -- Activate to open the door
        local ok, err = pcall(function() obj:activateBy(selfModule.object) end)
        if not ok then
            return {id = id, success = false, message = 'Failed to open door: ' .. tostring(err)}
        end
        -- Walk toward the door and through it
        currentAction = {
            type = 'approach',
            id = id,
            duration = 3.0,
            targetYaw = 0,
            targetName = target,
            targetObj = obj,
            stopDist = 50, -- walk right up to and through the door
        }
        actionTimer = 0
        return {id = id, success = true, message = 'Opening door and walking through'}

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

    elseif action == 'approach' then
        local target = params.target
        if not target then
            return {id = id, success = false, message = 'No target specified'}
        end
        local obj, dist = findNearbyByName(target, nearby.actors, nearby.doors, nearby.items, nearby.containers, nearby.activators)
        if not obj then
            return {id = id, success = false, message = 'Could not find: ' .. target}
        end
        local ppos = selfModule.object.position
        local opos = obj.position

        if dist < 300 then
            -- Already very close — just face and walk straight
            local targetYaw = math.atan2(opos.x - ppos.x, opos.y - ppos.y)
            currentAction = {
                type = 'approach',
                id = id,
                duration = 2.0,
                targetYaw = targetYaw,
                targetName = target,
                targetObj = obj,
                stopDist = 200,
            }
            actionTimer = 0
            return {id = id, success = true, message = 'Approaching ' .. target .. ' (' .. math.floor(dist) .. ' units away)'}
        end

        -- For longer distances, use navmesh but target a point OFFSET toward the player
        -- (200 units in front of the door, toward us) so we don't path into walls
        local util = require('openmw.util')
        local toPlayer = util.vector3(ppos.x - opos.x, ppos.y - opos.y, 0)
        local len = toPlayer:length()
        local approachPos
        if len > 1 then
            -- Point 200 units in front of the door, toward the player
            local offset = toPlayer * (200 / len)
            approachPos = {x = opos.x + offset.x, y = opos.y + offset.y, z = opos.z}
        else
            approachPos = {x = opos.x, y = opos.y, z = opos.z}
        end

        local navigation = require('scripts/claude_bridge/navigation')
        local result = navigation.startNavigation(approachPos)
        if result.success then
            navigation.setActionId(id)
            return {id = id, success = true, message = 'Navigating to ' .. target .. ' (' .. math.floor(dist) .. ' units away)'}
        else
            -- Navmesh failed — fall back to straight-line walk
            local targetYaw = math.atan2(opos.x - ppos.x, opos.y - ppos.y)
            currentAction = {
                type = 'approach',
                id = id,
                duration = math.min(dist / 300, 10.0),
                targetYaw = targetYaw,
                targetName = target,
                targetObj = obj,
                stopDist = 200,
            }
            actionTimer = 0
            return {id = id, success = true, message = 'Walking toward ' .. target .. ' (' .. math.floor(dist) .. ' units away, no path found)'}
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
            if types.Weapon and types.Weapon.objectIsInstance(item) then
                slot = Actor.EQUIPMENT_SLOT.CarriedRight
            elseif types.Armor and types.Armor.objectIsInstance(item) then
                local armorRecord = types.Armor.record(item)
                local armorType = armorRecord.type
                local AT = types.Armor.TYPE
                local ES = Actor.EQUIPMENT_SLOT
                local armorSlotMap = {
                    [AT.Helmet] = ES.Helmet,
                    [AT.Cuirass] = ES.Cuirass,
                    [AT.LPauldron] = ES.LeftPauldron,
                    [AT.RPauldron] = ES.RightPauldron,
                    [AT.Greaves] = ES.Greaves,
                    [AT.Boots] = ES.Boots,
                    [AT.LGauntlet] = ES.LeftGauntlet,
                    [AT.RGauntlet] = ES.RightGauntlet,
                    [AT.Shield] = ES.CarriedLeft,
                    [AT.LBracer] = ES.LeftGauntlet,
                    [AT.RBracer] = ES.RightGauntlet,
                }
                slot = armorSlotMap[armorType]
            elseif types.Clothing and types.Clothing.objectIsInstance(item) then
                local clothRecord = types.Clothing.record(item)
                local clothType = clothRecord.type
                local CT = types.Clothing.TYPE
                local ES = Actor.EQUIPMENT_SLOT
                local clothSlotMap = {
                    [CT.Shirt] = ES.Shirt,
                    [CT.Pants] = ES.Pants,
                    [CT.Shoes] = ES.Boots,
                    [CT.Skirt] = ES.Skirt,
                    [CT.Robe] = ES.Robe,
                    [CT.Ring] = ES.LeftRing,
                    [CT.Amulet] = ES.Amulet,
                    [CT.Belt] = ES.Belt,
                    [CT.LGlove] = ES.LeftGauntlet,
                    [CT.RGlove] = ES.RightGauntlet,
                }
                slot = clothSlotMap[clothType]
            end
        end
        if slot then
            local eqp = Actor.getEquipment(selfModule.object)
            eqp[slot] = item
            Actor.setEquipment(selfModule, eqp)
            return {id = id, success = true, message = 'Equipped: ' .. itemName}
        else
            return {id = id, success = false, message = 'Could not determine equipment slot for: ' .. itemName}
        end

    elseif action == 'face_target' then
        local target = params.target
        if not target then
            return {id = id, success = false, message = 'No target specified'}
        end
        local obj = findNearbyByName(target, nearby.actors, nearby.doors, nearby.items, nearby.containers, nearby.activators)
        if not obj then
            return {id = id, success = false, message = 'Could not find: ' .. target}
        end
        local ppos = selfModule.object.position
        local opos = obj.position
        local targetYaw = math.atan2(opos.x - ppos.x, opos.y - ppos.y)
        currentAction = {
            type = 'turn',
            id = id,
            angle = 0, -- will be computed per-frame
            duration = 0.5,
            faceTarget = obj,
        }
        actionTimer = 0
        return {id = id, success = true, message = 'Facing ' .. target}

    elseif action == 'attack' then
        -- Use the engine's AiCombat package for the player
        local targetName = params.target
        if not targetName then
            return {id = id, success = false, message = 'No target specified'}
        end
        local obj = findNearbyByName(targetName, nearby.actors)
        if not obj then
            return {id = id, success = false, message = 'Could not find: ' .. targetName}
        end
        local maxDuration = params.duration or 15.0
        local meleeOnly = params.melee_only
        if meleeOnly == nil then meleeOnly = true end
        -- If melee_only, force weapon stance so AI won't waste time on spells
        if meleeOnly then
            Actor.setStance(selfModule, Actor.STANCE.Weapon)
        end
        -- Override combat controls so the Lua playercontrols script doesn't
        -- reset controls.use=0 every frame (which cancels AI attacks)
        pcall(function() I.Controls.overrideCombatControls(true) end)
        -- Start AI combat package — the engine handles movement, facing, and attacks
        local ok, err = pcall(function()
            selfModule:_startAiCombat(obj, true)
        end)
        if not ok then
            return {id = id, success = false, message = 'Failed to start AI combat: ' .. tostring(err)}
        end
        print('Claude bridge: AI combat started against ' .. targetName)
        currentAction = {
            type = 'attack',
            id = id,
            duration = maxDuration,
            target = targetName,
            targetObj = obj,
            meleeOnly = meleeOnly,
        }
        actionTimer = 0
        local mode = meleeOnly and 'melee only' or 'auto (magic + melee)'
        return {id = id, success = true, message = 'Fighting ' .. targetName .. ' (' .. mode .. ')'}

    elseif action == 'cast' then
        local spellInput = params.spell
        if not spellInput then
            return {id = id, success = false, message = 'No spell specified'}
        end
        -- Resolve spell: try as record ID first, then search by name
        local foundSpell = nil
        local ok, actorSpells = pcall(function() return Actor.spells(selfModule.object) end)
        if ok and actorSpells then
            for _, spell in pairs(actorSpells) do
                if spell.id == spellInput then
                    foundSpell = spell
                    break
                end
            end
            if not foundSpell then
                -- Search by name (case-insensitive)
                local searchName = spellInput:lower()
                for _, spell in pairs(actorSpells) do
                    if spell.name and spell.name:lower():find(searchName, 1, true) then
                        foundSpell = spell
                        break
                    end
                end
            end
        end
        if not foundSpell then
            return {id = id, success = false, message = 'Spell not found: ' .. spellInput}
        end
        Actor.setSelectedSpell(selfModule, foundSpell)
        -- Override combat controls so playercontrols.lua doesn't reset use=0
        pcall(function() I.Controls.overrideCombatControls(true) end)
        Actor.setStance(selfModule, Actor.STANCE.Spell)
        currentAction = {
            type = 'cast',
            id = id,
            duration = 2.0,
            stanceReady = false,
            triggerUse = true,  -- signal to trigger the Use action
            target = params.target,
            spellName = foundSpell.name or foundSpell.id,
        }
        actionTimer = 0
        return {id = id, success = true, message = 'Casting ' .. (foundSpell.name or foundSpell.id)}

    elseif action == 'stop' then
        resetControls()
        currentAction = nil
        return {id = id, success = true, message = 'Stopped'}

    elseif action == 'wait' then
        local duration = params.duration or 1.0
        currentAction = {type = 'wait', id = id, duration = duration}
        actionTimer = 0
        return {id = id, success = true, message = 'Waiting'}

    elseif action == 'save_game' then
        local bridge = require('openmw.bridge')
        local desc = params.description or 'Agent save'
        local ok, err = pcall(function() bridge.saveGame(desc) end)
        if ok then
            return {id = id, success = true, message = 'Game saved: ' .. desc}
        else
            return {id = id, success = false, message = 'Save failed: ' .. tostring(err)}
        end

    elseif action == 'load_game' then
        local bridge = require('openmw.bridge')
        local desc = params.description or ''
        local ok, found = pcall(function() return bridge.loadGame(desc) end)
        if ok and found then
            return {id = id, success = true, message = 'Loading save: ' .. desc}
        elseif ok then
            return {id = id, success = false, message = 'No save found matching: ' .. desc}
        else
            return {id = id, success = false, message = 'Load failed: ' .. tostring(found)}
        end

    elseif action == 'list_saves' then
        local bridge = require('openmw.bridge')
        local ok, saves = pcall(function() return bridge.listSaves() end)
        if ok and saves then
            local saveList = {}
            for i, s in ipairs(saves) do
                saveList[i] = s.description .. ' (level ' .. tostring(s.playerLevel) .. ')'
            end
            local msg = #saveList > 0 and table.concat(saveList, ', ') or 'No saves found'
            return {id = id, success = #saveList > 0, message = msg, saves = saves}
        else
            return {id = id, success = false, message = 'Failed to list saves'}
        end

    elseif action == 'quicksave' then
        local bridge = require('openmw.bridge')
        local desc = params.description or 'Agent quicksave'
        local ok, err = pcall(function() bridge.quickSave(desc) end)
        if ok then
            return {id = id, success = true, message = 'Quicksaved: ' .. desc}
        else
            return {id = id, success = false, message = 'Quicksave failed: ' .. tostring(err)}
        end

    elseif action == 'quickload' then
        local bridge = require('openmw.bridge')
        local ok, err = pcall(function() bridge.quickLoad() end)
        if ok then
            return {id = id, success = true, message = 'Loading quicksave...'}
        else
            return {id = id, success = false, message = 'Quickload failed: ' .. tostring(err)}
        end

    elseif action == 'use_item' then
        local itemName = params.item
        if not itemName then
            return {id = id, success = false, message = 'No item specified'}
        end
        local item = findInventoryItem(itemName)
        if not item then
            return {id = id, success = false, message = 'Item not found in inventory: ' .. itemName}
        end
        local bridge = require('openmw.bridge')
        local ok, err = pcall(function() bridge.useItem(item.recordId) end)
        if ok then
            return {id = id, success = true, message = 'Used: ' .. itemName}
        else
            return {id = id, success = false, message = 'Failed to use item: ' .. tostring(err)}
        end

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
        local topicName = params.topic
        if not topicName then
            return {id = id, success = false, message = 'No topic specified'}
        end
        local bridge = require('openmw.bridge')
        if not bridge.isDialogueOpen() then
            return {id = id, success = false, message = 'No dialogue is open'}
        end
        local response = bridge.selectDialogueTopic(topicName)
        if response and response.text then
            return {id = id, success = true, message = response.text, title = response.title}
        else
            local errMsg = (response and response[2]) or ('No response for topic: ' .. topicName)
            return {id = id, success = false, message = errMsg}
        end

    elseif action == 'get_available_topics' then
        local bridge = require('openmw.bridge')
        if not bridge.isDialogueOpen() then
            return {id = id, success = false, message = 'No dialogue is open'}
        end
        local topics = bridge.getAvailableTopics()
        local topicList = {}
        for i, t in ipairs(topics) do
            topicList[i] = t
        end
        return {id = id, success = true, message = table.concat(topicList, ', '), topics = topicList}

    elseif action == 'get_choices' then
        local bridge = require('openmw.bridge')
        if not bridge.isDialogueOpen() then
            return {id = id, success = false, message = 'No dialogue is open'}
        end
        if not bridge.isInChoice() then
            return {id = id, success = false, message = 'NPC is not presenting a choice'}
        end
        local choices = bridge.getChoices()
        local choiceList = {}
        for i, c in ipairs(choices) do
            choiceList[i] = c.text .. ' (id:' .. c.id .. ')'
        end
        return {id = id, success = true, message = table.concat(choiceList, ', '), choices = choices}

    elseif action == 'answer_choice' then
        local choiceId = tonumber(params.choice_id)
        if choiceId == nil then
            return {id = id, success = false, message = 'No choice_id specified'}
        end
        local bridge = require('openmw.bridge')
        if not bridge.isDialogueOpen() then
            return {id = id, success = false, message = 'No dialogue is open'}
        end
        local response = bridge.answerChoice(choiceId)
        if response and response.text then
            return {id = id, success = true, message = response.text, title = response.title}
        else
            local errMsg = (response and response[2]) or 'No response for choice'
            return {id = id, success = false, message = errMsg}
        end

    elseif action == 'close_dialogue' then
        local bridge = require('openmw.bridge')
        bridge.closeDialogue()
        return {id = id, success = true, message = 'Dialogue closed'}

    elseif action == 'get_contents' then
        -- Read contents of a nearby container or dead actor's inventory
        local targetName = params.target
        if not targetName then
            return {id = id, success = false, message = 'No target specified'}
        end
        local obj = findNearbyByName(targetName, nearby.actors, nearby.containers)
        if not obj then
            return {id = id, success = false, message = 'Could not find: ' .. targetName}
        end
        local inv = nil
        local ok, err = pcall(function()
            if types.Container and types.Container.objectIsInstance and types.Container.objectIsInstance(obj) then
                inv = types.Container.content(obj)
            elseif Actor.objectIsInstance(obj) then
                inv = Actor.inventory(obj)
            end
        end)
        if not ok or not inv then
            return {id = id, success = false, message = 'Cannot read contents: ' .. tostring(err)}
        end
        local items = {}
        local count = 0
        for _, item in ipairs(inv:getAll()) do
            if count >= 30 then break end
            local okR, rec = pcall(function() return item.type.record(item) end)
            if okR and rec then
                count = count + 1
                items[count] = {
                    recordId = item.recordId,
                    name = rec.name or item.recordId,
                    count = item.count,
                    value = rec.value or 0,
                    weight = rec.weight or 0,
                }
            end
        end
        return {id = id, success = true, items = items, message = count .. ' items found'}

    elseif action == 'take_item' then
        -- Take an item from a nearby container or dead actor
        local targetName = params.target
        local itemName = params.item
        local takeCount = params.count or 1
        if not targetName or not itemName then
            return {id = id, success = false, message = 'Need target and item'}
        end
        local obj = findNearbyByName(targetName, nearby.actors, nearby.containers)
        if not obj then
            return {id = id, success = false, message = 'Could not find: ' .. targetName}
        end
        local inv = nil
        pcall(function()
            if types.Container and types.Container.objectIsInstance and types.Container.objectIsInstance(obj) then
                inv = types.Container.content(obj)
            elseif Actor.objectIsInstance(obj) then
                inv = Actor.inventory(obj)
            end
        end)
        if not inv then
            return {id = id, success = false, message = 'Cannot access inventory of: ' .. targetName}
        end
        -- Find the item
        local searchName = itemName:lower()
        local item = nil
        for _, obj2 in ipairs(inv:getAll()) do
            local okR, rec = pcall(function() return obj2.type.record(obj2) end)
            if okR and rec and rec.name and rec.name:lower():find(searchName, 1, true) then
                item = obj2
                break
            end
        end
        if not item then
            return {id = id, success = false, message = 'Item not found: ' .. itemName}
        end
        local okR, rec = pcall(function() return item.type.record(item) end)
        local playerInv = Actor.inventory(selfModule.object)
        local transferItem = item
        if takeCount < item.count then
            transferItem = item:split(takeCount)
        end
        transferItem:moveInto(playerInv)
        return {id = id, success = true, message = 'Took ' .. takeCount .. 'x ' .. (rec and rec.name or itemName)}

    elseif action == 'drop_item' then
        -- Drop an item from player inventory onto the ground
        local itemName = params.item
        local dropCount = params.count or 1
        if not itemName then
            return {id = id, success = false, message = 'No item specified'}
        end
        local item = findInventoryItem(itemName)
        if not item then
            return {id = id, success = false, message = 'Item not found in inventory: ' .. itemName}
        end
        local okR, rec = pcall(function() return item.type.record(item) end)
        local name = (okR and rec and rec.name) or item.recordId
        local dropItem = item
        if dropCount < item.count then
            dropItem = item:split(dropCount)
        end
        dropItem:teleport(selfModule.object.cell, selfModule.object.position)
        return {id = id, success = true, message = 'Dropped ' .. dropCount .. 'x ' .. name}

    elseif action == 'take_all' then
        -- Take all items from a nearby container or dead actor
        local targetName = params.target
        if not targetName then
            return {id = id, success = false, message = 'No target specified'}
        end
        local obj = findNearbyByName(targetName, nearby.actors, nearby.containers)
        if not obj then
            return {id = id, success = false, message = 'Could not find: ' .. targetName}
        end
        local inv = nil
        pcall(function()
            if types.Container and types.Container.objectIsInstance and types.Container.objectIsInstance(obj) then
                inv = types.Container.content(obj)
            elseif Actor.objectIsInstance(obj) then
                inv = Actor.inventory(obj)
            end
        end)
        if not inv then
            return {id = id, success = false, message = 'Cannot access inventory of: ' .. targetName}
        end
        local playerInv = Actor.inventory(selfModule.object)
        local taken = {}
        for _, item in ipairs(inv:getAll()) do
            local okR, rec = pcall(function() return item.type.record(item) end)
            local name = (okR and rec and rec.name) or item.recordId
            local cnt = item.count
            pcall(function() item:moveInto(playerInv) end)
            taken[#taken + 1] = cnt > 1 and (cnt .. 'x ' .. name) or name
        end
        return {id = id, success = true, message = 'Took ' .. #taken .. ' items: ' .. table.concat(taken, ', ')}

    elseif action == 'check_journal' then
        local questId = params.quest
        local ok, result = pcall(function()
            local journal = Player.journal(selfModule.object)
            if not journal or not journal.journalTextEntries then
                return {id = id, success = false, message = 'No journal available'}
            end
            local entries = {}
            for i = 1, #journal.journalTextEntries do
                local e = journal.journalTextEntries[i]
                if questId == nil or questId == '' or e.questId == questId then
                    entries[#entries + 1] = {
                        text = e.text,
                        questId = e.questId or '',
                        day = e.day,
                        month = e.month,
                    }
                end
            end
            return {id = id, success = true, entries = entries, message = #entries .. ' journal entries'}
        end)
        if ok then
            return result
        else
            return {id = id, success = false, message = 'Failed to read journal: ' .. tostring(result)}
        end

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

    elseif action == 'get_merchant_inventory' then
        local npcName = params.npc
        if not npcName then
            return {id = id, success = false, message = 'No NPC specified'}
        end
        local npc = findTarget(npcName, nearby.actors)
        if not npc then
            return {id = id, success = false, message = 'NPC not found nearby: ' .. npcName}
        end
        -- Check this NPC offers Barter
        local okR, npcRecord = pcall(function() return types.NPC.record(npc) end)
        if not okR or not npcRecord or not npcRecord.servicesOffered or not npcRecord.servicesOffered.Barter then
            return {id = id, success = false, message = npcName .. ' is not a merchant'}
        end
        -- Read merchant inventory
        local inv = Actor.inventory(npc):getAll()
        local items = {}
        local count = 0
        for _, item in pairs(inv) do
            if count >= 40 then break end
            local okI, rec = pcall(function() return item.type.record(item) end)
            if okI and rec then
                count = count + 1
                items[count] = {
                    recordId = item.recordId,
                    name = rec.name or item.recordId,
                    count = item.count,
                    value = rec.value or 0,
                    weight = rec.weight or 0,
                }
            end
        end
        -- Get merchant gold
        local okG, barterGold = pcall(function() return Actor.getBarterGold(npc) end)
        local gold = okG and barterGold or 0
        return {id = id, success = true, message = 'Merchant has ' .. count .. ' item types', items = items, merchantGold = gold}

    elseif action == 'buy_item' then
        local npcName = params.npc
        local itemId = params.item
        local buyCount = params.count or 1
        if not npcName or not itemId then
            return {id = id, success = false, message = 'Need npc and item'}
        end
        local npc = findTarget(npcName, nearby.actors)
        if not npc then
            return {id = id, success = false, message = 'NPC not found nearby: ' .. npcName}
        end
        -- Find item in merchant inventory
        local searchName = itemId:lower()
        local merchantInv = Actor.inventory(npc)
        local item = nil
        for _, obj in ipairs(merchantInv:getAll()) do
            local okI, rec = pcall(function() return obj.type.record(obj) end)
            if okI and rec and rec.name and rec.name:lower():find(searchName, 1, true) then
                item = obj
                break
            end
        end
        if not item then
            return {id = id, success = false, message = 'Item not found in merchant inventory: ' .. itemId}
        end
        local okI, rec = pcall(function() return item.type.record(item) end)
        local price = (okI and rec and rec.value) or 0
        local totalCost = price * buyCount
        -- Check player has enough gold
        local playerInv = Actor.inventory(selfModule.object)
        local playerGold = playerInv:countOf('gold_001')
        if playerGold < totalCost then
            return {id = id, success = false, message = 'Not enough gold. Need ' .. totalCost .. ', have ' .. playerGold}
        end
        -- Transfer item to player
        local transferItem = item
        if buyCount < item.count then
            transferItem = item:split(buyCount)
        end
        transferItem:moveInto(playerInv)
        -- Deduct gold from player to merchant
        if totalCost > 0 then
            local goldStack = nil
            for _, obj in ipairs(playerInv:getAll()) do
                if obj.recordId == 'gold_001' then goldStack = obj break end
            end
            if goldStack then
                local payment = goldStack:split(totalCost)
                payment:moveInto(merchantInv)
            end
        end
        return {id = id, success = true, message = 'Bought ' .. buyCount .. 'x ' .. (rec and rec.name or itemId) .. ' for ' .. totalCost .. ' gold'}

    elseif action == 'sell_item' then
        local npcName = params.npc
        local itemName = params.item
        local sellCount = params.count or 1
        if not npcName or not itemName then
            return {id = id, success = false, message = 'Need npc and item'}
        end
        local npc = findTarget(npcName, nearby.actors)
        if not npc then
            return {id = id, success = false, message = 'NPC not found nearby: ' .. npcName}
        end
        -- Find item in player inventory
        local item = findInventoryItem(itemName)
        if not item then
            return {id = id, success = false, message = 'Item not found in inventory: ' .. itemName}
        end
        local okI, rec = pcall(function() return item.type.record(item) end)
        local price = (okI and rec and rec.value) or 0
        -- Sell price is typically half the base value
        local sellPrice = math.floor(price * 0.5)
        local totalValue = sellPrice * sellCount
        -- Check merchant has enough gold
        local okG, barterGold = pcall(function() return Actor.getBarterGold(npc) end)
        local mGold = okG and barterGold or 0
        if mGold < totalValue then
            return {id = id, success = false, message = 'Merchant cannot afford this. Sell value: ' .. totalValue .. ', merchant gold: ' .. mGold}
        end
        -- Transfer item to merchant
        local merchantInv = Actor.inventory(npc)
        local transferItem = item
        if sellCount < item.count then
            transferItem = item:split(sellCount)
        end
        transferItem:moveInto(merchantInv)
        -- Give gold to player
        if totalValue > 0 then
            local playerInv = Actor.inventory(selfModule.object)
            -- Find gold in merchant inventory
            local goldStack = nil
            for _, obj in ipairs(merchantInv:getAll()) do
                if obj.recordId == 'gold_001' then goldStack = obj break end
            end
            if goldStack and goldStack.count >= totalValue then
                local payment = goldStack:split(totalValue)
                payment:moveInto(playerInv)
            else
                -- Merchant may not have physical gold objects, create via barter gold
                pcall(function() Actor.setBarterGold(npc, mGold - totalValue) end)
                -- Create gold for player
                local world = require('openmw.core')
                local goldObj = world.createObject('gold_001', totalValue)
                goldObj:moveInto(playerInv)
            end
        end
        return {id = id, success = true, message = 'Sold ' .. sellCount .. 'x ' .. (rec and rec.name or itemName) .. ' for ' .. totalValue .. ' gold'}

    elseif action == 'rest' then
        local hours = params.hours or 1
        local bridge = require('openmw.bridge')
        local ok, result = pcall(function() return bridge.rest(hours) end)
        if ok and result and result.success then
            return {id = id, success = true, message = result.message}
        else
            return {id = id, success = false, message = 'Rest failed: ' .. tostring(result)}
        end

    elseif action == 'persuade' then
        local persuadeAction = params.action
        if not persuadeAction then
            return {id = id, success = false, message = 'No persuasion action specified'}
        end
        local bridge = require('openmw.bridge')
        if not bridge.isDialogueOpen() then
            return {id = id, success = false, message = 'No dialogue is open — must be talking to an NPC'}
        end
        -- Map action string to PersuasionType enum value
        local typeMap = {
            admire = 0,      -- PT_Admire
            intimidate = 1,  -- PT_Intimidate
            taunt = 2,       -- PT_Taunt
            bribe10 = 3,     -- PT_Bribe10
            bribe100 = 4,    -- PT_Bribe100
            bribe1000 = 5,   -- PT_Bribe1000
        }
        local pType = typeMap[persuadeAction:lower()]
        if pType == nil then
            return {id = id, success = false, message = 'Unknown persuasion action: ' .. persuadeAction .. '. Use: admire, intimidate, taunt, bribe10, bribe100, bribe1000'}
        end
        local ok, result = pcall(function() return bridge.persuade(pType) end)
        if ok and result and result.success then
            return {id = id, success = true, message = result.message, text = result.text, actionName = result.actionName}
        else
            local errMsg = (ok and result and result.message) or tostring(result)
            return {id = id, success = false, message = 'Persuasion failed: ' .. errMsg}
        end

    elseif action == 'get_training_services' then
        local npcName = params.npc
        if not npcName then
            return {id = id, success = false, message = 'No NPC name specified'}
        end
        local bridge = require('openmw.bridge')
        local ok, result = pcall(function() return bridge.getTrainingServices(npcName) end)
        if ok and result then
            local services = {}
            for i = 1, #result do
                local s = result[i]
                table.insert(services, s.skillName .. ' (price:' .. s.price .. ', NPC:' .. s.npcLevel .. ', yours:' .. s.playerLevel .. ', can_train:' .. tostring(s.canTrain) .. ')')
            end
            if #services > 0 then
                return {id = id, success = true, message = table.concat(services, '; '), services = result}
            else
                return {id = id, success = false, message = npcName .. ' does not offer training services or is not nearby'}
            end
        else
            return {id = id, success = false, message = 'Failed to get training services: ' .. tostring(result)}
        end

    elseif action == 'train_skill' then
        local npcName = params.npc
        local skillName = params.skill
        if not npcName then
            return {id = id, success = false, message = 'No NPC name specified'}
        end
        if not skillName then
            return {id = id, success = false, message = 'No skill name specified'}
        end
        local bridge = require('openmw.bridge')
        local ok, result = pcall(function() return bridge.trainSkill(npcName, skillName) end)
        if ok and result and result.success then
            return {id = id, success = true, message = result.message, skillName = result.skillName, newLevel = result.newLevel, price = result.price}
        else
            local errMsg = (ok and result and result.message) or tostring(result)
            return {id = id, success = false, message = 'Training failed: ' .. errMsg}
        end

    elseif action == 'brew_potion' then
        local potionName = params.name
        local ingredientIds = params.ingredients
        if not potionName or potionName == '' then
            return {id = id, success = false, message = 'No potion name specified'}
        end
        if not ingredientIds or #ingredientIds < 2 then
            return {id = id, success = false, message = 'Need at least 2 ingredient record IDs'}
        end
        local bridge = require('openmw.bridge')
        local ingTable = {}
        for i, v in ipairs(ingredientIds) do
            ingTable[i] = v
        end
        local ok, result = pcall(function() return bridge.brewPotion(potionName, ingTable) end)
        if ok and result then
            return {id = id, success = result.success, message = result.message, result = result.result}
        else
            return {id = id, success = false, message = 'Brew failed: ' .. tostring(result)}
        end

    elseif action == 'preview_potion' then
        local ingredientIds = params.ingredients
        if not ingredientIds or #ingredientIds < 2 then
            return {id = id, success = false, message = 'Need at least 2 ingredient record IDs'}
        end
        local bridge = require('openmw.bridge')
        local ingTable = {}
        for i, v in ipairs(ingredientIds) do
            ingTable[i] = v
        end
        local ok, result = pcall(function() return bridge.previewPotion(ingTable) end)
        if ok and result then
            local effectNames = {}
            if result.effects then
                for i, eff in ipairs(result.effects) do
                    effectNames[i] = eff.name or '?'
                end
            end
            local msg = result.message or ''
            if #effectNames > 0 then
                msg = msg .. ': ' .. table.concat(effectNames, ', ')
            end
            return {id = id, success = result.success, message = msg, effects = result.effects}
        else
            return {id = id, success = false, message = 'Preview failed: ' .. tostring(result)}
        end

    elseif action == 'pick_lock' then
        local targetName = params.target
        if not targetName then
            return {id = id, success = false, message = 'No target specified'}
        end
        local bridge = require('openmw.bridge')
        local ok, result = pcall(function() return bridge.pickLock(targetName) end)
        if ok and result then
            return {
                id = id,
                success = result.success or false,
                message = result.message or 'Pick lock attempted',
                lockLevel = result.lockLevel,
                usesLeft = result.usesLeft,
            }
        else
            return {id = id, success = false, message = 'Pick lock failed: ' .. tostring(result)}
        end

    elseif action == 'disarm_trap' then
        local targetName = params.target
        if not targetName then
            return {id = id, success = false, message = 'No target specified'}
        end
        local bridge = require('openmw.bridge')
        local ok, result = pcall(function() return bridge.disarmTrap(targetName) end)
        if ok and result then
            return {
                id = id,
                success = result.success or false,
                message = result.message or 'Disarm trap attempted',
                usesLeft = result.usesLeft,
            }
        else
            return {id = id, success = false, message = 'Disarm trap failed: ' .. tostring(result)}
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
        -- Hybrid approach: AI combat handles movement/pathfinding,
        -- we manually handle facing and weapon swings via controls.use
        local obj = act.targetObj or findNearbyByName(act.target, nearby.actors)

        -- Force weapon stance every frame
        if act.meleeOnly then
            Actor.setStance(selfModule, Actor.STANCE.Weapon)
        end

        -- Face and approach target manually (AI movement can be unreliable for player)
        if obj and obj.position then
            faceTarget(obj, dt)
            local dist = (obj.position - selfModule.object.position):length()
            if dist > 200 then
                selfModule.controls.movement = 1
                selfModule.controls.run = false
            end
        end

        -- Swing weapon: hold use=1 to charge, release to swing
        if not act.swingTimer then act.swingTimer = 0 end
        if not act.swingPhase then act.swingPhase = 'charge' end
        act.swingTimer = act.swingTimer + dt
        if act.swingPhase == 'charge' then
            selfModule.controls.use = 1
            if act.swingTimer >= 0.6 then
                act.swingPhase = 'release'
                act.swingTimer = 0
            end
        elseif act.swingPhase == 'release' then
            selfModule.controls.use = 0
            if act.swingTimer >= 0.5 then
                act.swingPhase = 'charge'
                act.swingTimer = 0
            end
        end

        -- Check if target is dead
        if obj then
            local ok2, dead = pcall(function() return Actor.isDead(obj) end)
            if ok2 and dead then
                pcall(function() selfModule:_iterateAndFilterAiSequence(function() return false end) end)
                resetControls()
                currentAction = nil
                return {
                    type = 'action_complete', id = act.id, success = true,
                    message = act.target .. ' is dead!'
                }
            end
        end

        -- Check fatigue — stop if below 50%
        local ok, fatigue = pcall(function() return Actor.stats.dynamic.fatigue(selfModule.object) end)
        if ok and fatigue then
            local fatigueRatio = fatigue.current / math.max(fatigue.base, 1)
            if fatigueRatio < 0.5 then
                pcall(function() selfModule:_iterateAndFilterAiSequence(function() return false end) end)
                resetControls()
                currentAction = nil
                return {
                    type = 'action_complete', id = act.id, success = true,
                    message = 'Stopped attacking — fatigue low (' .. math.floor(fatigue.current) .. '/' .. math.floor(fatigue.base) .. ')'
                }
            end
        end

        -- Check player health — stop if critically low
        local ok3, health = pcall(function() return Actor.stats.dynamic.health(selfModule.object) end)
        if ok3 and health then
            local healthRatio = health.current / math.max(health.base, 1)
            if healthRatio < 0.2 then
                pcall(function() selfModule:_iterateAndFilterAiSequence(function() return false end) end)
                resetControls()
                currentAction = nil
                return {
                    type = 'action_complete', id = act.id, success = false,
                    message = 'Health critically low (' .. math.floor(health.current) .. '/' .. math.floor(health.base) .. ') — need to heal!'
                }
            end
        end

        -- Check if AI combat ended on its own (e.g. target fled out of range)
        local ok4, activePkg = pcall(function() return selfModule:_getActiveAiPackage() end)
        if ok4 and activePkg == nil then
            resetControls()
            currentAction = nil
            return {
                type = 'action_complete', id = act.id, success = true,
                message = 'Combat ended (AI package completed)'
            }
        end

    elseif act.type == 'cast' then
        -- Auto-face target while casting
        if act.target then
            local obj = findNearbyByName(act.target, nearby.actors)
            if obj then
                faceTarget(obj, dt)
            end
        end
        -- Spell cast: ensure vanilla controls are enabled, set use=1 for one frame
        -- The vanilla processAttacking() reads controls.use for spell stance
        if not act.stanceReady then
            -- Frame 1: stance switch, ensure controls not overridden
            act.stanceReady = true
            selfModule.controls.use = 0
        elseif not act.castFired then
            -- Frame 2: trigger the cast
            act.castFired = true
            selfModule.controls.use = 1
            print('Claude bridge: Spell cast triggered (use=1)')
        else
            -- Frame 3+: release
            selfModule.controls.use = 0
        end

    elseif act.type == 'jump' then
        -- jump flag was set, just wait to clear

    elseif act.type == 'approach' then
        -- Face target and walk toward it
        local obj = act.targetObj
        if obj and obj.position then
            faceTarget(obj, dt)
            selfModule.controls.movement = 1
            -- Walk when close (combat range), run when far
            local dist = (obj.position - selfModule.object.position):length()
            selfModule.controls.run = dist > 1000
            if dist < act.stopDist then
                resetControls()
                currentAction = nil
                return {type = 'action_complete', id = act.id, success = true, message = 'Reached ' .. act.targetName}
            end
        end

    elseif act.type == 'wait' then
        -- do nothing, just wait
    end

    -- Check if action duration elapsed
    if actionTimer >= act.duration then
        if act.type == 'attack' then
            pcall(function() selfModule:_iterateAndFilterAiSequence(function() return false end) end)
        end
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

-- Cancel current action and reset controls
function actions.cancelCurrent()
    if currentAction then
        resetControls()
        currentAction = nil
    end
end

return actions
