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
local dialogue = require('scripts/claude_bridge/dialogue')
local navigation = require('scripts/claude_bridge/navigation')

-- Configuration
local BRIDGE_PORT = 21003
local OBSERVATION_INTERVAL = 0.2  -- seconds between observations
local MAX_NEARBY = 30  -- max objects per category
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

-- Helper: compass direction from player to object
local function directionFromPlayer(obj)
    local ppos = selfModule.object.position
    local opos = obj.position
    if not opos then return '' end
    local dx = opos.x - ppos.x
    local dy = opos.y - ppos.y
    local angle = math.atan2(dx, dy) -- radians, 0=north, pi/2=east
    -- Convert to compass direction
    local deg = math.deg(angle)
    if deg < 0 then deg = deg + 360 end
    if deg < 22.5 or deg >= 337.5 then return 'N'
    elseif deg < 67.5 then return 'NE'
    elseif deg < 112.5 then return 'E'
    elseif deg < 157.5 then return 'SE'
    elseif deg < 202.5 then return 'S'
    elseif deg < 247.5 then return 'SW'
    elseif deg < 292.5 then return 'W'
    else return 'NW'
    end
end

-- Helper: sort objects by distance and cap count
local function nearestObjects(objectList, maxCount)
    local sorted = {}
    for _, obj in pairs(objectList) do
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

-- Helper: compass from yaw angle
local function yawToCompass(yaw)
    local deg = math.deg(yaw)
    if deg < 0 then deg = deg + 360 end
    if deg < 22.5 or deg >= 337.5 then return 'N'
    elseif deg < 67.5 then return 'NE'
    elseif deg < 112.5 then return 'E'
    elseif deg < 157.5 then return 'SE'
    elseif deg < 202.5 then return 'S'
    elseif deg < 247.5 then return 'SW'
    elseif deg < 292.5 then return 'W'
    else return 'NW'
    end
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

    -- Facing direction
    local ok1, yaw = pcall(camera.getYaw)
    local ok2, pitch = pcall(camera.getPitch)
    if ok1 then
        state.cameraYaw = yaw
        state.facing = yawToCompass(yaw)
    end
    if ok2 then state.cameraPitch = pitch end

    -- Crime/bounty
    local ok3, bounty = pcall(function() return Player.getCrimeLevel(obj) end)
    if ok3 then state.bounty = bounty end

    -- Encumbrance
    local okE, enc = pcall(function() return Actor.getEncumbrance(obj) end)
    local okC, cap = pcall(function() return Actor.getCapacity(obj) end)
    if okE and okC then
        state.encumbrance = math.floor(enc)
        state.capacity = math.floor(cap)
    end

    -- Birth sign
    local ok4, sign = pcall(function() return Player.getBirthSign(obj) end)
    if ok4 and sign then state.birthSign = sign end

    return state
end

-- Build attributes
local function buildAttributes()
    local obj = selfModule.object
    local attrs = {}
    local attrNames = {'strength', 'intelligence', 'willpower', 'agility', 'speed', 'endurance', 'personality', 'luck'}
    for _, name in ipairs(attrNames) do
        local ok, stat = pcall(function() return Actor.stats.attributes[name](obj) end)
        if ok and stat then
            attrs[name] = {base = stat.base, modified = stat.modified}
        end
    end
    return attrs
end

-- Build skills
local function buildSkills()
    local obj = selfModule.object
    local skills = {}
    local skillNames = {
        'block', 'armorer', 'mediumarmor', 'heavyarmor', 'bluntweapon', 'longblade', 'axe', 'spear',
        'athletics', 'enchant', 'destruction', 'alteration', 'illusion', 'conjuration', 'mysticism', 'restoration',
        'alchemy', 'unarmored', 'security', 'sneak', 'acrobatics', 'lightarmor', 'shortblade', 'marksman',
        'mercantile', 'speechcraft', 'handtohand',
    }
    for _, name in ipairs(skillNames) do
        local ok, stat = pcall(function() return Actor.stats.skills[name](obj) end)
        if ok and stat then
            -- Only include skills with base > 5 to reduce noise
            if stat.base > 5 then
                skills[name] = {base = stat.base, modified = stat.modified}
            end
        end
    end
    return skills
end

-- Build active effects (buffs, debuffs, diseases)
local function buildActiveEffects()
    local obj = selfModule.object
    local effects = {}
    local ok, activeEffects = pcall(function() return Actor.activeEffects(obj) end)
    if ok and activeEffects then
        local count = 0
        for _, effect in pairs(activeEffects) do
            if count >= 20 then break end
            count = count + 1
            local entry = {}
            pcall(function()
                entry.id = effect.id
                entry.magnitude = effect.magnitude
                entry.affectedAttribute = effect.affectedAttribute
                entry.affectedSkill = effect.affectedSkill
            end)
            effects[count] = entry
        end
    end
    return effects
end

-- Build known spells list with effect descriptions
local function buildSpells()
    local obj = selfModule.object
    local magic = core.magic
    local spells = {}
    local ok, actorSpells = pcall(function() return Actor.spells(obj) end)
    if ok and actorSpells then
        local count = 0
        for _, spell in pairs(actorSpells) do
            if count >= 30 then break end
            count = count + 1
            local entry = {
                id = spell.id,
                name = spell.name or spell.id,
                cost = spell.cost,
            }
            -- Spell type
            if spell.type == magic.SPELL_TYPE.Power then
                entry.spellType = 'power'
                local ok3, canUse = pcall(function() return actorSpells:canUsePower(spell) end)
                if ok3 then entry.canUse = canUse end
            elseif spell.type == magic.SPELL_TYPE.Ability then
                entry.spellType = 'ability'
            elseif spell.type == magic.SPELL_TYPE.Spell then
                entry.spellType = 'spell'
            end
            -- Effect descriptions
            if spell.effects then
                local effects = {}
                for _, eff in pairs(spell.effects) do
                    local effEntry = {}
                    pcall(function()
                        local effRecord = magic.effects.records[eff.effect.id]
                        if effRecord then
                            effEntry.name = effRecord.name
                        end
                        effEntry.id = eff.effect.id
                        effEntry.magnitudeMin = eff.magnitudeMin
                        effEntry.magnitudeMax = eff.magnitudeMax
                        effEntry.range = eff.range
                        effEntry.duration = eff.duration
                    end)
                    effects[#effects + 1] = effEntry
                end
                if #effects > 0 then entry.effects = effects end
            end
            spells[count] = entry
        end
    end
    -- Selected spell
    local ok2, selected = pcall(function() return Actor.getSelectedSpell(obj) end)
    if ok2 and selected then
        spells._selected = selected.id or selected
    end
    return spells
end

-- Build faction membership
local function buildFactions()
    local obj = selfModule.object
    local factions = {}
    local ok, factionList = pcall(function() return types.NPC.getFactions(obj) end)
    if ok and factionList then
        for _, factionId in pairs(factionList) do
            local ok2, rank = pcall(function() return types.NPC.getFactionRank(obj, factionId) end)
            local ok3, rep = pcall(function() return types.NPC.getFactionReputation(obj, factionId) end)
            factions[factionId] = {
                rank = ok2 and rank or 0,
                reputation = ok3 and rep or 0,
            }
        end
    end
    return factions
end

-- Build time and weather info
local function buildEnvironment()
    local obj = selfModule.object
    local env = {}

    -- Game time
    local ok1, gameTime = pcall(function() return core.getGameTime() end)
    if ok1 then
        local hours = math.floor(gameTime / 3600) % 24
        local minutes = math.floor(gameTime / 60) % 60
        env.time = string.format('%02d:%02d', hours, minutes)
        -- Time of day description
        if hours >= 6 and hours < 12 then env.timeOfDay = 'morning'
        elseif hours >= 12 and hours < 17 then env.timeOfDay = 'afternoon'
        elseif hours >= 17 and hours < 21 then env.timeOfDay = 'evening'
        else env.timeOfDay = 'night'
        end
    end

    -- Weather
    if obj.cell and not obj.cell.isInterior then
        local ok2, weather = pcall(function() return core.weather.getCurrentWeather(obj.cell) end)
        if ok2 and weather then
            env.weather = weather.name or 'unknown'
        end
    end

    return env
end

-- Build equipment info
local function buildEquipment()
    local eqp = Actor.getEquipment(selfModule.object)
    local result = {}
    for slot, item in pairs(eqp) do
        local entry = {
            recordId = item.recordId,
            name = getRecordName(item),
        }
        -- Add armor rating
        local okA, isArmor = pcall(function() return types.Armor and types.Armor.objectIsInstance(item) end)
        if okA and isArmor then
            local okR, rec = pcall(function() return types.Armor.record(item) end)
            if okR and rec then
                entry.armorRating = rec.baseArmor
                entry.weight = rec.weight
            end
        end
        -- Add weapon damage
        local okW, isWeapon = pcall(function() return types.Weapon and types.Weapon.objectIsInstance(item) end)
        if okW and isWeapon then
            local okR, rec = pcall(function() return types.Weapon.record(item) end)
            if okR and rec then
                entry.chopDamage = {rec.chopMinDamage, rec.chopMaxDamage}
                entry.slashDamage = {rec.slashMinDamage, rec.slashMaxDamage}
                entry.thrustDamage = {rec.thrustMinDamage, rec.thrustMaxDamage}
                entry.weight = rec.weight
                entry.speed = rec.speed
            end
        end
        result[tostring(slot)] = entry
    end
    return result
end

-- Build inventory summary with potion/scroll effects
local function buildInventory()
    local inv = Actor.inventory(selfModule.object):getAll()
    local magic = core.magic
    local items = {}
    local count = 0
    for _, item in pairs(inv) do
        if count >= MAX_INVENTORY then break end
        count = count + 1
        local entry = {
            recordId = item.recordId,
            name = getRecordName(item),
            count = item.count,
        }
        -- Add armor rating for inventory items
        local okA, isArmor = pcall(function() return types.Armor and types.Armor.objectIsInstance(item) end)
        if okA and isArmor then
            local okR, rec = pcall(function() return types.Armor.record(item) end)
            if okR and rec then
                entry.itemType = 'armor'
                entry.armorRating = rec.baseArmor
                entry.weight = rec.weight
            end
        end
        -- Add weapon damage for inventory items
        local okW, isWeapon = pcall(function() return types.Weapon and types.Weapon.objectIsInstance(item) end)
        if okW and isWeapon then
            local okR, rec = pcall(function() return types.Weapon.record(item) end)
            if okR and rec then
                entry.itemType = 'weapon'
                entry.chopDamage = {rec.chopMinDamage, rec.chopMaxDamage}
                entry.slashDamage = {rec.slashMinDamage, rec.slashMaxDamage}
                entry.thrustDamage = {rec.thrustMinDamage, rec.thrustMaxDamage}
                entry.weight = rec.weight
                entry.speed = rec.speed
            end
        end
        -- Add effects for potions
        if types.Potion and types.Potion.objectIsInstance and pcall(function() return types.Potion.objectIsInstance(item) end) and types.Potion.objectIsInstance(item) then
            local ok, record = pcall(function() return types.Potion.record(item) end)
            if ok and record and record.effects then
                local effects = {}
                for _, eff in pairs(record.effects) do
                    pcall(function()
                        local effRecord = magic.effects.records[eff.effect.id]
                        effects[#effects + 1] = {
                            name = effRecord and effRecord.name or tostring(eff.effect.id),
                            magnitude = eff.magnitudeMin or 0,
                        }
                    end)
                end
                if #effects > 0 then
                    entry.itemType = 'potion'
                    entry.effects = effects
                end
            end
        end
        -- Detect ingredients and show known effects based on Alchemy skill
        local okIng, isIngredient = pcall(function() return types.Ingredient and types.Ingredient.objectIsInstance(item) end)
        if okIng and isIngredient then
            local okR, rec = pcall(function() return types.Ingredient.record(item) end)
            if okR and rec then
                entry.itemType = 'ingredient'
                entry.value = rec.value or 0
                entry.weight = rec.weight or 0
                -- Get player's alchemy skill to determine known effects
                local alchemySkill = 0
                pcall(function()
                    local skillStat = Actor.stats.skills.alchemy(selfModule.object)
                    alchemySkill = skillStat.modified or skillStat.base or 0
                end)
                if rec.effects then
                    local effects = {}
                    for i, eff in ipairs(rec.effects) do
                        -- fWortChanceValue defaults to 15; effects known at skill >= fWortChanceValue * ceil(i/2)
                        local threshold = 15 * math.ceil(i / 2)
                        if alchemySkill >= threshold then
                            pcall(function()
                                local effRecord = magic.effects.records[eff.effect.id]
                                effects[#effects + 1] = {
                                    name = effRecord and effRecord.name or tostring(eff.effect.id),
                                    index = i,
                                }
                            end)
                        else
                            effects[#effects + 1] = {name = '???', index = i}
                        end
                    end
                    if #effects > 0 then
                        entry.effects = effects
                    end
                end
            end
        end
        -- Detect apparatus items (Mortar & Pestle, Alembic, Calcinator, Retort)
        local okApp, isApparatus = pcall(function() return types.Apparatus and types.Apparatus.objectIsInstance(item) end)
        if okApp and isApparatus then
            entry.itemType = 'apparatus'
            local okR, rec = pcall(function() return types.Apparatus.record(item) end)
            if okR and rec then
                entry.quality = rec.quality
                entry.weight = rec.weight
                entry.value = rec.value
                if rec.type == types.Apparatus.TYPE.MortarPestle then
                    entry.apparatusType = 'Mortar & Pestle'
                elseif rec.type == types.Apparatus.TYPE.Alembic then
                    entry.apparatusType = 'Alembic'
                elseif rec.type == types.Apparatus.TYPE.Calcinator then
                    entry.apparatusType = 'Calcinator'
                elseif rec.type == types.Apparatus.TYPE.Retort then
                    entry.apparatusType = 'Retort'
                end
            end
        end
        items[count] = entry
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
                direction = directionFromPlayer(obj),
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
            -- NPC-specific info
            if types.NPC.objectIsInstance(obj) then
                -- Disposition
                local ok4, disp = pcall(function() return types.NPC.getDisposition(obj) end)
                if ok4 then entry.disposition = math.floor(disp) end
                -- Services
                local ok5, record = pcall(function() return types.NPC.record(obj) end)
                if ok5 and record and record.servicesOffered then
                    local services = {}
                    for svc, offered in pairs(record.servicesOffered) do
                        if offered then services[#services + 1] = svc end
                    end
                    if #services > 0 then entry.services = services end
                end
                -- Faction
                if ok5 and record and record.primaryFaction and record.primaryFaction ~= '' then
                    entry.faction = record.primaryFaction
                end
                -- Race/class
                if ok5 and record then
                    entry.race = record.race
                    entry.class = record.class
                end
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
            direction = directionFromPlayer(obj),
        }
        -- Door destination
        local ok, isTeleport = pcall(function() return types.Door.isTeleport(obj) end)
        if ok and isTeleport then
            entry.isTeleport = true
            local ok2, destCell = pcall(function() return types.Door.destCell(obj) end)
            if ok2 and destCell then
                entry.destination = destCell.name ~= '' and destCell.name or destCell.id
            end
        end
        -- Lock and trap info
        local okL, isLockable = pcall(function() return types.Lockable and types.Lockable.objectIsInstance(obj) end)
        if okL and isLockable then
            local okLocked, locked = pcall(function() return types.Lockable.isLocked(obj) end)
            if okLocked and locked then
                entry.locked = true
                local okLevel, level = pcall(function() return types.Lockable.getLockLevel(obj) end)
                if okLevel and level then
                    entry.lockLevel = level
                end
            end
            local okTrap, trapSpell = pcall(function() return types.Lockable.getTrapSpell(obj) end)
            if okTrap and trapSpell and trapSpell ~= nil then
                entry.trapped = true
            end
        end
        result[#result + 1] = entry
    end
    return result
end

-- Build nearby items info
local function buildNearbyItems()
    local items = nearestObjects(nearby.items, MAX_NEARBY)
    local result = {}
    for _, obj in ipairs(items) do
        local entry = {
            id = tostring(obj.id),
            name = getRecordName(obj),
            recordId = obj.recordId,
            position = {x = obj.position.x, y = obj.position.y, z = obj.position.z},
            distance = math.floor(distanceFromPlayer(obj)),
            count = obj.count,
        }
        -- Check ownership
        local okOwner, ownerRecordId = pcall(function()
            if obj.owner and obj.owner.recordId then
                return obj.owner.recordId
            end
            return nil
        end)
        local okFaction, ownerFaction = pcall(function()
            if obj.owner and obj.owner.factionId then
                return obj.owner.factionId
            end
            return nil
        end)
        if (okOwner and ownerRecordId and ownerRecordId ~= '') or (okFaction and ownerFaction and ownerFaction ~= '') then
            entry.owned = true
        end
        result[#result + 1] = entry
    end
    return result
end

-- Build nearby containers info
local function buildNearbyContainers()
    local containers = nearestObjects(nearby.containers, MAX_NEARBY)
    local result = {}
    for _, obj in ipairs(containers) do
        local entry = {
            id = tostring(obj.id),
            name = getRecordName(obj),
            recordId = obj.recordId,
            position = {x = obj.position.x, y = obj.position.y, z = obj.position.z},
            distance = math.floor(distanceFromPlayer(obj)),
            direction = directionFromPlayer(obj),
        }
        -- Check ownership
        local okOwner, ownerRecordId = pcall(function()
            if obj.owner and obj.owner.recordId then
                return obj.owner.recordId
            end
            return nil
        end)
        local okFaction, ownerFaction = pcall(function()
            if obj.owner and obj.owner.factionId then
                return obj.owner.factionId
            end
            return nil
        end)
        if (okOwner and ownerRecordId and ownerRecordId ~= '') or (okFaction and ownerFaction and ownerFaction ~= '') then
            entry.owned = true
        end
        -- Lock and trap info
        local okL, isLockable = pcall(function() return types.Lockable and types.Lockable.objectIsInstance(obj) end)
        if okL and isLockable then
            local okLocked, locked = pcall(function() return types.Lockable.isLocked(obj) end)
            if okLocked and locked then
                entry.locked = true
                local okLevel, level = pcall(function() return types.Lockable.getLockLevel(obj) end)
                if okLevel and level then
                    entry.lockLevel = level
                end
            end
            local okTrap, trapSpell = pcall(function() return types.Lockable.getTrapSpell(obj) end)
            if okTrap and trapSpell and trapSpell ~= nil then
                entry.trapped = true
            end
        end
        result[#result + 1] = entry
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
            direction = directionFromPlayer(obj),
        }
    end
    return result
end

-- Build quest info
local function buildQuests()
    local result = {}
    local ok, quests = pcall(function() return Player.quests(selfModule.object) end)
    if ok and quests then
        local ok2, _ = pcall(function()
            for i = 1, #quests do
                local quest = quests[i]
                result[#result + 1] = {
                    id = quest.id,
                    stage = quest.stage,
                    finished = quest.finished,
                }
            end
        end)
    end
    -- Include recent journal text entries
    local journalTexts = {}
    local ok3, _ = pcall(function()
        local journal = Player.journal(selfModule.object)
        if journal and journal.journalTextEntries then
            local entries = journal.journalTextEntries
            local startIdx = math.max(1, #entries - 9)
            for i = startIdx, #entries do
                local e = entries[i]
                journalTexts[#journalTexts + 1] = {
                    text = e.text,
                    questId = e.questId,
                }
            end
        end
    end)
    return result, journalTexts
end

-- Build full observation
-- Send a message over the bridge
local function bridgeSend(msg)
    bridge.send(json.encode(msg))
end

-- Combat hit tracking
local lastHits = {}
local MAX_HITS = 5
local combatTrackingInitialized = false

local function initCombatTracking()
    if combatTrackingInitialized then return end
    combatTrackingInitialized = true
    local ok, _ = pcall(function()
        I.Combat.addOnHitHandler(function(attack)
            local entry = {
                time = core.getSimulationTime(),
                damage = attack.damage or {},
                successful = attack.successful,
                sourceType = attack.sourceType,
            }
            if attack.attacker then
                local ok2, record = pcall(function() return attack.attacker.type.record(attack.attacker) end)
                entry.attackerName = (ok2 and record and record.name) or (attack.attacker.recordId or 'unknown')
            end
            if attack.weapon then
                local ok3, record = pcall(function() return attack.weapon.type.record(attack.weapon) end)
                entry.weaponName = (ok3 and record and record.name) or nil
            end
            if entry.sourceType == 'magic' then
                local magic = core.magic
                local effects = {}
                pcall(function()
                    for _, eff in pairs(Actor.activeEffects(selfModule.object)) do
                        local effRecord = magic.effects.records[eff.id]
                        if effRecord and eff.magnitude and eff.magnitude ~= 0 then
                            effects[#effects + 1] = effRecord.name
                        end
                    end
                end)
                entry.magicEffects = effects
            end
            lastHits[#lastHits + 1] = entry
            if #lastHits > MAX_HITS then
                table.remove(lastHits, 1)
            end
        end)
    end)
    if not ok then
        print('Claude bridge: Failed to register combat hit handler')
    end
end

local function getRecentHits()
    local result = {}
    for i, hit in ipairs(lastHits) do
        result[i] = hit
    end
    return result
end

local function buildObservation()
    local questList, journalTexts = buildQuests()
    return {
        type = 'observation',
        timestamp = core.getSimulationTime(),
        player = buildPlayerState(),
        attributes = buildAttributes(),
        skills = buildSkills(),
        activeEffects = buildActiveEffects(),
        spells = buildSpells(),
        factions = buildFactions(),
        environment = buildEnvironment(),
        equipment = buildEquipment(),
        inventory = buildInventory(),
        nearby = {
            actors = buildNearbyActors(),
            doors = buildNearbyDoors(),
            items = buildNearbyItems(),
            containers = buildNearbyContainers(),
            activators = buildNearbyActivators(),
        },
        quests = questList,
        journalTexts = journalTexts,
        currentAction = actions.getCurrentAction(),
        dialogue = dialogue.getDialogueState(selfModule.object),
        recentHits = getRecentHits(),
    }
end

-- bridgeSend moved earlier in the file

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
                elseif cmd.action == 'navigate_to' then
                    local result = navigation.startNavigation(cmd.params.destination or cmd.params.target)
                    if result.success then
                        navigation.setActionId(cmd.id)
                    end
                    bridgeSend({type = 'action_result', id = cmd.id, success = result.success, message = result.message})
                elseif cmd.action == 'navigate_to_npc' then
                    local result = navigation.startNavigationToNPC(cmd.params.npc or cmd.params.target)
                    if result.success then
                        navigation.setActionId(cmd.id)
                    end
                    bridgeSend({type = 'action_result', id = cmd.id, success = result.success, message = result.message})
                elseif cmd.action == 'get_travel_destinations' then
                    local destinations = bridge.getTravelDestinations(cmd.params.npc or '')
                    local destList = {}
                    for i, d in ipairs(destinations) do
                        destList[i] = d.name .. ' (' .. d.price .. ' gold)'
                    end
                    local msg = #destList > 0 and table.concat(destList, ', ') or 'No travel destinations available'
                    bridgeSend({type = 'action_result', id = cmd.id, success = #destList > 0, message = msg, destinations = destinations})
                elseif cmd.action == 'travel' then
                    local result = bridge.travel(cmd.params.npc or '', cmd.params.destination or '')
                    bridgeSend({type = 'action_result', id = cmd.id, success = result.success, message = result.message})
                elseif cmd.action == 'cancel_navigation' then
                    navigation.cancelNavigation()
                    bridgeSend({type = 'action_result', id = cmd.id, success = true, message = 'Navigation cancelled'})
                elseif cmd.action == 'read_book' then
                    local result = actions.processCommand(cmd)
                    if result then
                        result.type = 'action_result'
                        bridgeSend(result)
                    end
                elseif cmd.action == 'screenshot' then
                    local result = actions.processCommand(cmd)
                    if result then
                        result.type = 'action_result'
                        bridgeSend(result)
                    end
                else
                    -- Local player action
                    local result = actions.processCommand(cmd)
                    if result then
                        -- Forward the full result including extra fields (choices, title, topics, etc.)
                        result.type = 'action_result'
                        bridgeSend(result)
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
local gameJustLoaded = false

local function onFrame(dt)
    if not initialized then
        bridge.start(BRIDGE_PORT)
        initialized = true
        gameJustLoaded = true
        initCombatTracking()
        print('Claude bridge: listening on port ' .. BRIDGE_PORT)
    end

    if not bridge.isConnected() then
        -- Clean up any stale AI combat packages so manual play isn't blocked
        local currentType = actions.getCurrentAction and actions.getCurrentAction()
        if currentType then
            pcall(function() selfModule:_iterateAndFilterAiSequence(function() return false end) end)
            pcall(function() I.Controls.overrideCombatControls(false) end)
            pcall(function() I.Controls.overrideUiControls(false) end)
            actions.cancelCurrent()
            print('Claude bridge: cleaned up stale action (' .. tostring(currentType) .. ') on disconnect')
        end
        -- Poll once to accept pending connections
        bridge.poll()
        return
    end

    -- Auto-reload on death: detect death, clean up, notify Python, and reload.
    local okDead, isDead = pcall(function() return Actor.isDead(selfModule.object) end)
    if okDead and isDead then
        -- Clean up AI combat and controls FIRST to prevent crash during load
        pcall(function() selfModule:_iterateAndFilterAiSequence(function() return false end) end)
        pcall(function() I.Controls.overrideCombatControls(false) end)
        pcall(function() I.Controls.overrideUiControls(false) end)
        actions.cancelCurrent()
        -- Notify Python
        bridgeSend({type = 'player_died'})
        -- Auto-load most recent save (safe now that AI is cleaned up)
        print('Claude bridge: player died, auto-loading last save')
        local bridgeModule = require('openmw.bridge')
        pcall(function() bridgeModule.loadGame('') end)
        return
    end

    -- Auto level-up: detect level-up readiness and handle it automatically
    -- This prevents the level-up dialog from blocking the bridge
    local okLvl, levelStat = pcall(function() return Player.stats.level(selfModule.object) end)
    if okLvl and levelStat and levelStat.progress and levelStat.progress >= 10 then
        print('Claude bridge: level-up available, auto-leveling...')
        local bridgeModule = require('openmw.bridge')
        local okUp, result = pcall(function() return bridgeModule.autoLevelUp() end)
        if okUp and result and result.success then
            print('Claude bridge: ' .. result.message)
            local increased = result.increased or {}
            for i = 1, #increased do
                local a = increased[i]
                if a then
                    print('  ' .. (a.name or '?') .. ' +' .. (a.increase or 0) .. ' -> ' .. (a.newValue or 0))
                end
            end
            bridgeSend({type = 'level_up', level = result.level, increased = result.increased})
        else
            print('Claude bridge: auto level-up failed: ' .. tostring(result))
        end
    end

    -- Notify Python after a game load so it can flush stale command waits
    if gameJustLoaded then
        gameJustLoaded = false
        bridgeSend({type = 'game_loaded'})
        -- Send an immediate observation so Python gets fresh state
        local ok, obs = pcall(buildObservation)
        if ok then bridgeSend(obs) end
    end

    -- Process incoming commands
    processIncoming()

    -- Update current action
    local completion = actions.update(dt)
    if completion then
        bridgeSend(completion)
    end

    -- Update navigation
    if navigation.isNavigating() then
        local navMessages = navigation.updateNavigation(dt)
        if navMessages then
            for _, msg in ipairs(navMessages) do
                bridgeSend(msg)
            end
        end
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

local function onLoad()
    -- After a game load, Lua state is reset but the C++ bridge socket persists.
    -- Notify Python that a load happened so it can flush stale waits.
    -- We can't send immediately (bridge.poll hasn't run yet), so flag it.
    initialized = false  -- force re-init on next frame
end

local function onSave()
    -- Nothing to persist — bridge state is transient
    return {}
end

return {
    engineHandlers = {
        onFrame = onFrame,
        onLoad = onLoad,
        onSave = onSave,
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
        DialogueResponse = function(data)
            local msg = dialogue.handleDialogueResponse(data)
            if msg then
                pendingResults[#pendingResults + 1] = msg
            end
        end,
    },
}
