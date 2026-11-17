#include "TelescopeZmqEventSource.h"

#include <JANA/JApplication.h>
#include <JANA/JEventSourceGeneratorT.h>

extern "C" {
void InitPlugin(JApplication *application) {
  InitJANAPlugin(application);
  application->Add(new JEventSourceGeneratorT<TelescopeZmqEventSource>());
}
}
