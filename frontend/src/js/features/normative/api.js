// frontend/src/js/features/normative/api.js

/**
 * Public API Gateway client managed catalog.
 */

import {
  NORMATIVE_ENDPOINT,
} from "../../config.js";


async function extractErrorMessage(
  response,
) {
  try {
    const payload = await response.json();

    if (
      payload
      && typeof payload.detail === "string"
    ) {
      return payload.detail;
    }

    if (payload?.detail) {
      return JSON.stringify(
        payload.detail,
      );
    }

  } catch {
    // Ниже используем HTTP status.
  }

  return `HTTP ${response.status}`;
}


async function requestJson(
  path,
  options = {},
) {
  const response = await fetch(
    `${NORMATIVE_ENDPOINT}${path}`,
    options,
  );

  if (!response.ok) {
    const message = await extractErrorMessage(
      response,
    );

    throw new Error(
      message,
    );
  }

  return response.json();
}


function jsonRequest(
  method,
  body,
) {
  return {
    method,

    headers: {
      "Content-Type": "application/json",
    },

    body: JSON.stringify(
      body,
    ),
  };
}


export function listSections() {
  return requestJson(
    "/sections",
  );
}


export function getSection(
  sectionId,
) {
  return requestJson(
    `/sections/${encodeURIComponent(sectionId)}`,
  );
}


export function createSection(
  name,
) {
  return requestJson(
    "/sections",
    jsonRequest(
      "POST",
      {
        name,
      },
    ),
  );
}


export function updateSection(
  sectionId,
  changes,
) {
  return requestJson(
    `/sections/${encodeURIComponent(sectionId)}`,
    jsonRequest(
      "PATCH",
      changes,
    ),
  );
}


export function deleteSection(
  sectionId,
) {
  return requestJson(
    `/sections/${encodeURIComponent(sectionId)}`,
    {
      method: "DELETE",
    },
  );
}


export function listCategories(
  sectionId,
) {
  return requestJson(
    `/sections/${encodeURIComponent(sectionId)}/categories`,
  );
}


export function createCategory(
  sectionId,
  {
    name,
    parentId = null,
  },
) {
  return requestJson(
    `/sections/${encodeURIComponent(sectionId)}/categories`,
    jsonRequest(
      "POST",
      {
        name,
        parent_id: parentId,
      },
    ),
  );
}


export function updateCategory(
  categoryId,
  changes,
) {
  return requestJson(
    `/categories/${encodeURIComponent(categoryId)}`,
    jsonRequest(
      "PATCH",
      changes,
    ),
  );
}


export function deleteCategory(
  categoryId,
) {
  return requestJson(
    `/categories/${encodeURIComponent(categoryId)}`,
    {
      method: "DELETE",
    },
  );
}


export function listDocuments(
  sectionId,
) {
  return requestJson(
    `/sections/${encodeURIComponent(sectionId)}/documents`,
  );
}


export async function uploadDocument(
  sectionId,
  {
    file,
    categoryId = null,
  },
) {
  const body = new FormData();

  body.append(
    "file",
    file,
  );

  if (categoryId) {
    body.append(
      "category_id",
      categoryId,
    );
  }

  return requestJson(
    `/sections/${encodeURIComponent(sectionId)}/documents`,
    {
      method: "POST",
      body,
    },
  );
}


export function queueDocument(
  documentId,
) {
  return requestJson(
    `/documents/${encodeURIComponent(documentId)}/index`,
    {
      method: "POST",
    },
  );
}


export function moveDocument(
  documentId,
  categoryId,
) {
  return requestJson(
    `/documents/${encodeURIComponent(documentId)}`,
    jsonRequest(
      "PATCH",
      {
        category_id: categoryId,
      },
    ),
  );
}


export function deleteDocument(
  documentId,
) {
  return requestJson(
    `/documents/${encodeURIComponent(documentId)}`,
    {
      method: "DELETE",
    },
  );
}


export function documentContentUrl(
  documentId,
) {
  return (
    `${NORMATIVE_ENDPOINT}/documents/`
    + `${encodeURIComponent(documentId)}/content`
  );
}


export function listUserPackageCategories(
  sectionId,
) {
  return requestJson(
    (
      `/sections/${encodeURIComponent(sectionId)}`
      + "/user-packages/categories"
    ),
  );
}


export function createUserPackageCategory(
  sectionId,
  {
    name,
    parentId = null,
  },
) {
  return requestJson(
    (
      `/sections/${encodeURIComponent(sectionId)}`
      + "/user-packages/categories"
    ),
    jsonRequest(
      "POST",
      {
        name,
        parent_id: parentId,
      },
    ),
  );
}


export function updateUserPackageCategory(
  categoryId,
  changes,
) {
  return requestJson(
    (
      "/user-packages/categories/"
      + `${encodeURIComponent(categoryId)}`
    ),
    jsonRequest(
      "PATCH",
      changes,
    ),
  );
}


export function deleteUserPackageCategory(
  categoryId,
) {
  return requestJson(
    (
      "/user-packages/categories/"
      + `${encodeURIComponent(categoryId)}`
    ),
    {
      method: "DELETE",
    },
  );
}


export function listUserPackageDocuments(
  sectionId,
) {
  return requestJson(
    (
      `/sections/${encodeURIComponent(sectionId)}`
      + "/user-packages/documents"
    ),
  );
}


export async function uploadUserPackageDocument(
  sectionId,
  {
    file,
    categoryId = null,
  },
) {
  const body = new FormData();

  body.append(
    "file",
    file,
  );

  if (categoryId) {
    body.append(
      "category_id",
      categoryId,
    );
  }

  return requestJson(
    (
      `/sections/${encodeURIComponent(sectionId)}`
      + "/user-packages/documents"
    ),
    {
      method: "POST",
      body,
    },
  );
}


export function queueUserPackageDocument(
  documentId,
) {
  return requestJson(
    (
      "/user-packages/documents/"
      + `${encodeURIComponent(documentId)}/index`
    ),
    {
      method: "POST",
    },
  );
}


export function moveUserPackageDocument(
  documentId,
  categoryId,
) {
  return requestJson(
    (
      "/user-packages/documents/"
      + `${encodeURIComponent(documentId)}`
    ),
    jsonRequest(
      "PATCH",
      {
        category_id: categoryId,
      },
    ),
  );
}


export function deleteUserPackageDocument(
  documentId,
) {
  return requestJson(
    (
      "/user-packages/documents/"
      + `${encodeURIComponent(documentId)}`
    ),
    {
      method: "DELETE",
    },
  );
}


export function userPackageDocumentContentUrl(
  documentId,
) {
  return (
    `${NORMATIVE_ENDPOINT}/user-packages/documents/`
    + `${encodeURIComponent(documentId)}/content`
  );
}