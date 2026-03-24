-- Navigation/pathfinding module for the Claude bridge.
-- Provides high-level "walk to destination" over multiple frames using navmesh.

local selfModule = require('openmw.self')
local nearby = require('openmw.nearby')
local types = require('openmw.types')
local Actor = types.Actor

local nav = {}

-- Known locations (approximate world coordinates)
local LOCATIONS = {
    ['seyda neen'] = {x = -10669, y = -71700, z = 66},
    ['balmora'] = {x = -22832, y = -15895, z = 130},
    ['vivec'] = {x = 20019, y = -53259, z = 310},
    ['ald-ruhn'] = {x = -26490, y = 6470, z = 1350},
    ['caldera'] = {x = -10273, y = -3131, z = 1060},
    ['suran'] = {x = 3428, y = -48039, z = 66},
    ['pelagiad'] = {x = -13354, y = -42827, z = 195},
    ['gnisis'] = {x = -43804, y = 37337, z = 520},
    ['molag mar'] = {x = 33975, y = -56319, z = 165},
    ['sadrith mora'] = {x = 55890, y = 10087, z = 265},
    ['tel mora'] = {x = 56099, y = 36965, z = 100},
    ['maar gan'] = {x = -39988, y = 28630, z = 1070},
    ['ebonheart'] = {x = 1916, y = -71900, z = 40},
    ['hla oad'] = {x = -22791, y = -58036, z = 4},
}

-- Navigation state
local navState = nil  -- nil when not navigating

local WAYPOINT_REACH_DIST = 200   -- distance to consider a waypoint reached
local STUCK_TIME = 3.0            -- seconds without progress before stuck detection
local STUCK_DIST = 50             -- minimum distance to move in STUCK_TIME
local MAX_RETRIES = 3             -- max stuck retries before giving up
local PROGRESS_INTERVAL = 2.0     -- seconds between progress reports

-- Resolve a destination: can be {x,y,z} table or a location name string
function nav.resolveDestination(dest)
    if type(dest) == 'string' then
        local key = dest:lower()
        local loc = LOCATIONS[key]
        if loc then
            return loc, dest
        end
        -- Try partial match
        for name, pos in pairs(LOCATIONS) do
            if name:find(key, 1, true) then
                return pos, name
            end
        end
        return nil, nil
    elseif type(dest) == 'table' and dest.x and dest.y and dest.z then
        return dest, string.format('(%.0f, %.0f, %.0f)', dest.x, dest.y, dest.z)
    end
    return nil, nil
end

-- Get available location names
function nav.getLocationNames()
    local names = {}
    for name, _ in pairs(LOCATIONS) do
        names[#names + 1] = name
    end
    table.sort(names)
    return names
end

-- Start navigating to a destination
-- dest: {x, y, z} table or location name string
-- Returns: {success=bool, message=string}
function nav.startNavigation(dest)
    local targetPos, targetName = nav.resolveDestination(dest)
    if not targetPos then
        return {success = false, message = 'Unknown destination: ' .. tostring(dest)}
    end

    local playerPos = selfModule.object.position
    local sourceVec = playerPos
    local destVec = require('openmw.util').vector3(targetPos.x, targetPos.y, targetPos.z)

    -- Get agent bounds for pathfinding
    local agentBounds = Actor.getPathfindingAgentBounds(selfModule.object)

    -- Find path
    local status, path = nearby.findPath(sourceVec, destVec, {
        agentBounds = agentBounds,
        includeFlags = nearby.NAVIGATOR_FLAGS.Walk + nearby.NAVIGATOR_FLAGS.OpenDoor,
    })

    if status ~= nearby.FIND_PATH_STATUS.Success and status ~= nearby.FIND_PATH_STATUS.PartialPath then
        return {success = false, message = 'Path not found to ' .. targetName .. ' (status: ' .. tostring(status) .. ')'}
    end

    if not path or #path == 0 then
        return {success = false, message = 'Empty path to ' .. targetName}
    end

    navState = {
        targetName = targetName,
        targetPos = destVec,
        waypoints = path,
        currentWaypoint = 1,
        stuckTimer = 0,
        lastPosition = playerPos,
        lastProgressPosition = playerPos,
        retries = 0,
        progressTimer = 0,
        totalDistance = (destVec - sourceVec):length(),
        partial = (status == nearby.FIND_PATH_STATUS.PartialPath),
    }

    local msg = 'Navigating to ' .. targetName .. ' (' .. #path .. ' waypoints'
    if navState.partial then
        msg = msg .. ', partial path'
    end
    msg = msg .. ')'

    return {success = true, message = msg}
end

-- Calculate yaw angle to face a target position
local function yawToTarget(targetPos)
    local playerPos = selfModule.object.position
    local dx = targetPos.x - playerPos.x
    local dy = targetPos.y - playerPos.y
    return math.atan2(dx, dy)
end

-- Get current player yaw from rotation
local function getCurrentYaw()
    local rot = selfModule.object.rotation
    -- rotation is a Transform; extract yaw (rotation around Z axis)
    -- In OpenMW, the object's facing direction can be derived from rotation
    -- For simplicity, use atan2 on the forward direction
    local forward = rot:apply(require('openmw.util').vector3(0, 1, 0))
    return math.atan2(forward.x, forward.y)
end

-- Normalize angle to [-pi, pi]
local function normalizeAngle(angle)
    while angle > math.pi do angle = angle - 2 * math.pi end
    while angle < -math.pi do angle = angle + 2 * math.pi end
    return angle
end

-- Update navigation each frame
-- dt: frame delta time
-- Returns: nil (still navigating), or {type, id, success, message} when done/progress
function nav.updateNavigation(dt)
    if not navState then return nil end

    local messages = {}
    local playerPos = selfModule.object.position

    -- Check if we reached the final destination
    local distToGoal = (navState.targetPos - playerPos):length()
    if distToGoal < WAYPOINT_REACH_DIST then
        selfModule.controls.movement = 0
        selfModule.controls.yawChange = 0
        local result = {
            type = 'action_complete',
            id = navState.id or '',
            success = true,
            message = 'Arrived at ' .. navState.targetName,
        }
        navState = nil
        return {result}
    end

    -- Get current waypoint
    local wp = navState.waypoints[navState.currentWaypoint]
    if not wp then
        selfModule.controls.movement = 0
        selfModule.controls.yawChange = 0
        local result = {
            type = 'action_complete',
            id = navState.id or '',
            success = false,
            message = 'No more waypoints',
        }
        navState = nil
        return {result}
    end

    local distToWaypoint = (wp - playerPos):length()

    -- Advance to next waypoint if close enough
    if distToWaypoint < WAYPOINT_REACH_DIST then
        navState.currentWaypoint = navState.currentWaypoint + 1
        if navState.currentWaypoint > #navState.waypoints then
            -- Reached end of path
            selfModule.controls.movement = 0
            selfModule.controls.yawChange = 0
            local result = {
                type = 'action_complete',
                id = navState.id or '',
                success = true,
                message = 'Arrived at ' .. navState.targetName,
            }
            navState = nil
            return {result}
        end
        wp = navState.waypoints[navState.currentWaypoint]
        navState.stuckTimer = 0
        navState.lastProgressPosition = playerPos
    end

    -- Face the waypoint and move forward
    local targetYaw = yawToTarget(wp)
    local currentYaw = getCurrentYaw()
    local yawDiff = normalizeAngle(targetYaw - currentYaw)

    selfModule.controls.yawChange = yawDiff * 5.0 * dt  -- proportional turning
    selfModule.controls.movement = 1
    selfModule.controls.run = true

    -- If facing very wrong direction, slow down movement
    if math.abs(yawDiff) > 1.0 then
        selfModule.controls.movement = 0.3
    end

    -- Stuck detection
    navState.stuckTimer = navState.stuckTimer + dt
    if navState.stuckTimer >= STUCK_TIME then
        local movedDist = (playerPos - navState.lastProgressPosition):length()
        if movedDist < STUCK_DIST then
            navState.retries = navState.retries + 1
            if navState.retries > MAX_RETRIES then
                selfModule.controls.movement = 0
                selfModule.controls.yawChange = 0
                local result = {
                    type = 'action_complete',
                    id = navState.id or '',
                    success = false,
                    message = 'Stuck while navigating to ' .. navState.targetName,
                }
                navState = nil
                return {result}
            end
            -- Try to unstick: jump and sidestep
            selfModule.controls.jump = true
            selfModule.controls.sideMovement = (navState.retries % 2 == 0) and 1 or -1
        end
        navState.stuckTimer = 0
        navState.lastProgressPosition = playerPos
    end

    -- Progress reports
    navState.progressTimer = navState.progressTimer + dt
    if navState.progressTimer >= PROGRESS_INTERVAL then
        navState.progressTimer = 0
        messages[#messages + 1] = {
            type = 'navigation_progress',
            destination = navState.targetName,
            waypointsRemaining = #navState.waypoints - navState.currentWaypoint,
            distanceToGoal = math.floor(distToGoal),
        }
    end

    if #messages > 0 then
        return messages
    end
    return nil
end

-- Check if currently navigating
function nav.isNavigating()
    return navState ~= nil
end

-- Cancel navigation
function nav.cancelNavigation()
    if navState then
        selfModule.controls.movement = 0
        selfModule.controls.sideMovement = 0
        selfModule.controls.yawChange = 0
        navState = nil
    end
end

-- Set the action ID for the current navigation (for tracking)
function nav.setActionId(id)
    if navState then
        navState.id = id
    end
end

return nav
