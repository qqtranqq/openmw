#ifndef MWLUA_BRIDGEBINDINGS_H
#define MWLUA_BRIDGEBINDINGS_H

#include <sol/forward.hpp>

namespace MWLua
{
    struct Context;

    sol::table initBridgePackage(const Context& context);
}

#endif // MWLUA_BRIDGEBINDINGS_H
