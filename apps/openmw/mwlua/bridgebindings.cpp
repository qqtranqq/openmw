#include "bridgebindings.hpp"

#include <osg/Image>
#include <osgDB/WriteFile>

#include <components/debug/debuglog.hpp>
#include <components/lua/bridgesocket.hpp>

#include "../mwbase/environment.hpp"
#include "../mwbase/world.hpp"
#include "context.hpp"
#include "luamanagerimp.hpp"

namespace MWLua
{
    // Single shared bridge socket instance
    static std::unique_ptr<LuaUtil::BridgeSocket> sBridgeSocket;

    sol::table initBridgePackage(const Context& context)
    {
        sol::state_view lua = context.sol();
        sol::table api(lua, sol::create);

        api["start"] = [](uint16_t port) {
            if (!sBridgeSocket)
                sBridgeSocket = std::make_unique<LuaUtil::BridgeSocket>();
            sBridgeSocket->start(port);
        };

        api["stop"] = []() {
            if (sBridgeSocket)
                sBridgeSocket->stop();
        };

        api["isConnected"] = []() -> bool {
            return sBridgeSocket && sBridgeSocket->isConnected();
        };

        api["send"] = [](std::string_view msg) {
            if (sBridgeSocket)
                sBridgeSocket->send(msg);
        };

        api["poll"] = [luaState = context.mLua]() -> sol::table {
            sol::state_view lua = luaState->unsafeState();
            sol::table result(lua, sol::create);
            if (sBridgeSocket)
            {
                sBridgeSocket->update();
                auto messages = sBridgeSocket->poll();
                for (size_t i = 0; i < messages.size(); ++i)
                    result[i + 1] = std::move(messages[i]);
            }
            return result;
        };

        api["getPort"] = []() -> uint16_t {
            return sBridgeSocket ? sBridgeSocket->getPort() : 0;
        };

        api["screenshot"] = [luaManager = context.mLuaManager](
                                 const std::string& path, sol::optional<int> width, sol::optional<int> height) {
            int w = width.value_or(640);
            int h = height.value_or(480);
            std::string savePath = path;

            luaManager->addAction(
                [savePath, w, h]()
                {
                    try
                    {
                        osg::ref_ptr<osg::Image> image(new osg::Image);
                        MWBase::Environment::get().getWorld()->screenshot(image.get(), w, h);
                        osgDB::writeImageFile(*image, savePath);
                        Log(Debug::Info) << "Bridge: Screenshot saved to " << savePath;

                        if (sBridgeSocket && sBridgeSocket->isConnected())
                        {
                            std::string msg = R"({"type":"screenshot","path":")" + savePath
                                + R"(","width":)" + std::to_string(w)
                                + R"(,"height":)" + std::to_string(h) + "}";
                            sBridgeSocket->send(msg);
                            sBridgeSocket->update();
                        }
                    }
                    catch (const std::exception& e)
                    {
                        Log(Debug::Error) << "Bridge: Screenshot failed: " << e.what();
                        if (sBridgeSocket && sBridgeSocket->isConnected())
                        {
                            std::string msg = R"({"type":"screenshot_error","message":")"
                                + std::string(e.what()) + R"("})";
                            sBridgeSocket->send(msg);
                            sBridgeSocket->update();
                        }
                    }
                },
                "BridgeScreenshot");
        };

        return api;
    }
}
