# Rights review

**Licensed public distribution is not cleared.** No root project-wide license
has been assigned. The owner must confirm contributor/institutional authority
and select explicit compatible terms before publication. No license or new
SPDX declaration is invented by curation or technical validation.

## Preserved evidence

- Nine pinned EICrecon source copies retain original headers, exact upstream
  hashes, local patches, unchanged upstream NOTICE and LGPLv3 text. Six have
  explicit LGPL-3.0-or-later SPDX headers; three configuration/factory headers
  have no per-file SPDX in the inspected source.
- The upstream NOTICE refers to `LICENSE.md`, whereas the inspected revision
  and retained directory contain `LICENSE.LGPL3`. Its coverage requires
  authoritative clarification; it is not this project's blanket public-domain
  dedication.
- The independent legacy mathematical writer/reference retained here identifies
  its local implementation origin. Its inclusion does not restore the excluded
  historical +Z plugin/backend or establish distribution permission.
- Mille/Millepede-II are pinned official dependencies, fetched locally and
  excluded from Git. Their headers state GNU Library GPL version 2 or later.
  Mille's missing standalone `COPYING.LIB` needs review before dependency
  packaging.

See [THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md) for paths and pins. The
original development repositories retain notices for sources excluded from this
candidate; those exclusions do not rewrite historical licensing evidence.

## Decisions before publication

1. Establish original contribution ownership and contributor/institutional
   authority; choose a project license compatible with retained material.
2. Resolve the EICrecon NOTICE/license-name/scope ambiguity and copied files
   lacking per-file SPDX using authoritative upstream information.
3. Review required accompanying license texts and source/linking obligations
   for the intended source or binary distribution. The LGPLv3 text incorporates
   GPLv3 terms by reference; this inventory does not certify complete compliance.
4. Review dependency packaging separately if shipping sources, libraries,
   executables or container layers. Keep official sources and notices unchanged.
5. Review rights/provenance separately before publishing any historical or newly
   generated dataset. Research data and archives are outside this source tree.

Engineering/numerical success and scientific validation are distinct from
licensing. Neither grants distribution rights. A local review candidate can be
built and tested while these decisions remain open. No push, publication,
merge or Geometry activation is performed by this curation task.
